"""Multi-sheet nesting driver.

Two placement engines are available:

* ``rect``  - MaxRects packing of each part's bounding rectangle. Exact and
  fast; ideal for the rectangular panels that make up most cabinets.
* ``shape`` - bottom-left fill on a bitmap of the true outline. Handles
  angled/curved parts, lets parts interlock and lets small parts drop into
  large cut-outs.

``best`` runs both within the time budget and keeps the better layout.

Coordinates: the sheet's long side is X, the short side is Y, origin in the
lower-left corner. A placement maps a part's base-frame point p to
``rotate(p, rotation) + (x, y)``.
"""

import math
import random
import time
from dataclasses import dataclass, field

from . import geometry
from .raster import RasterSheet, ShapeRaster
from .rectpack import MaxRectsBin

_EPS = 1e-9


@dataclass
class NestPart:
    """A part's flat shape in its own base frame.

    The shape is the union of `polys` (convex polygons) and the regions
    enclosed by `outlines` (polylines that together form closed loops; filled
    even-odd so holes stay open). Either may be empty but not both."""
    key: str                 # geometry key, copies of the same part share it
    name: str
    polys: list = field(default_factory=list)
    outlines: list = field(default_factory=list)
    area: float = 0.0        # real material area, used for utilisation figures
    data: object = None      # caller payload, passed through untouched


@dataclass
class NestOptions:
    sheet_length: float
    sheet_width: float
    margin: float = 1.0      # min distance from parts to the sheet edge
    spacing: float = 0.6     # min distance between parts (tool diameter)
    tolerance: float = 0.0   # extra clearance for tessellation error
    fill: str = 'long'       # 'long': rows along the long side, 'short': along the short side
    rotations: tuple = (0, 90, 180, 270)
    method: str = 'best'     # 'best' | 'rect' | 'shape'
    time_budget: float = 3.0
    resolution: int = 2000   # raster cells along the long sheet side
    seed: int = 1
    progress: object = None  # callable(fraction, message) -> bool (False cancels)


@dataclass
class Placement:
    part: NestPart
    rotation: float
    x: float
    y: float
    bbox: tuple              # (xmin, ymin, xmax, ymax) on the sheet


@dataclass
class SheetLayout:
    index: int
    placements: list = field(default_factory=list)
    utilization: float = 0.0


@dataclass
class NestResult:
    sheets: list
    unplaced: list
    method: str
    attempts: int
    sheet_length: float
    sheet_width: float
    cancelled: bool = False


class _Cancelled(Exception):
    pass


class _Context:
    def __init__(self, parts, opt):
        self.opt = opt
        self.length = max(opt.sheet_length, opt.sheet_width)
        self.width = min(opt.sheet_length, opt.sheet_width)
        self.transposed = opt.fill == 'short'
        self.radius = max(opt.spacing, 0.0) / 2.0 + max(opt.tolerance, 0.0)
        self.origin = opt.margin - self.radius
        dl = self.length - 2 * opt.margin + 2 * self.radius
        dw = self.width - 2 * opt.margin + 2 * self.radius
        # "Algorithm space": both engines always fill along X keeping Y low.
        # For short-side filling the problem is mirrored over the diagonal.
        self.dom_w, self.dom_h = (dw, dl) if self.transposed else (dl, dw)
        self.cell = self.length / max(int(opt.resolution), 50)
        self.rotations = _unique_rotations(opt.rotations)
        self.parts = parts
        self._geo = {}
        self._shapes = {}

    def geo(self, part, rot):
        """(polys, outlines, bbox) of the rotated part in algorithm space."""
        key = (part.key, rot)
        g = self._geo.get(key)
        if g is None:
            polys = [geometry.rotate_points(poly, rot) for poly in part.polys]
            outlines = [geometry.rotate_points(line, rot) for line in part.outlines]
            if self.transposed:
                polys = [[(y, x) for x, y in poly] for poly in polys]
                outlines = [[(y, x) for x, y in line] for line in outlines]
            pts = [p for poly in polys for p in poly] + [p for line in outlines for p in line]
            g = (polys, outlines, geometry.bbox(pts))
            self._geo[key] = g
        return g

    def shape(self, part, rot):
        key = (part.key, rot)
        s = self._shapes.get(key)
        if s is None:
            polys, outlines, _ = self.geo(part, rot)
            s = ShapeRaster(polys, self.cell, self.radius, outlines)
            self._shapes[key] = s
        return s

    def to_placement(self, part, rot, tx, ty):
        """Convert an algorithm-space translation into a sheet placement."""
        bx0, by0, bx1, by1 = self.geo(part, rot)[2]
        box = (bx0 + tx, by0 + ty, bx1 + tx, by1 + ty)
        if self.transposed:
            tx, ty = ty, tx
            box = (box[1], box[0], box[3], box[2])
        return Placement(part, rot, tx, ty, box)


def _unique_rotations(rots):
    out = []
    for r in rots:
        r = float(r) % 360.0
        if r not in out:
            out.append(r)
    return out or [0.0]


# --------------------------------------------------------------------------
# Engines. Each returns (sheets, unplaced) where sheets is a list of lists of
# (part, rot, tx, ty) in algorithm space.
# --------------------------------------------------------------------------

def _run_rect(ctx, order, rule):
    r2 = 2 * ctx.radius
    rots = []
    seen = set()
    for rot in ctx.rotations:
        if rot % 180.0 not in seen:
            seen.add(rot % 180.0)
            rots.append(rot)
    bins, sheets, unplaced = [], [], []
    for part in order:
        placed = False
        for b, items in zip(bins, sheets):
            placed = _rect_try(ctx, b, items, part, rots, r2, rule)
            if placed:
                break
        if not placed:
            b = MaxRectsBin(ctx.dom_w, ctx.dom_h)
            items = []
            if _rect_try(ctx, b, items, part, rots, r2, rule):
                bins.append(b)
                sheets.append(items)
            else:
                unplaced.append(part)
    return sheets, unplaced


def _rect_try(ctx, b, items, part, rots, r2, rule):
    best = None
    for rot in rots:
        bx0, by0, bx1, by1 = ctx.geo(part, rot)[2]
        w, h = bx1 - bx0 + r2, by1 - by0 + r2
        found = b.find(w, h, rule)
        if found and (best is None or found[0] < best[0]):
            best = (found[0], found[1], found[2], rot, w, h, bx0, by0)
    if best is None:
        return False
    _, fx, fy, rot, w, h, bx0, by0 = best
    b.place(fx, fy, w, h)
    items.append((part, rot,
                  ctx.origin + fx + ctx.radius - bx0,
                  ctx.origin + fy + ctx.radius - by0))
    return True


def _run_shape(ctx, order, check):
    ncols = int(math.floor(ctx.dom_w / ctx.cell + _EPS))
    nrows = int(math.floor(ctx.dom_h / ctx.cell + _EPS))
    grids, sheets, free, unplaced = [], [], [], []
    for i, part in enumerate(order):
        check(i / max(len(order), 1))
        shapes = [(rot, ctx.shape(part, rot)) for rot in ctx.rotations]
        placed = False
        candidates = list(range(len(grids))) + [None]
        for gi in candidates:
            if gi is None:
                grid = RasterSheet(ncols, nrows)
                gfree = ncols * nrows
            else:
                grid, gfree = grids[gi], free[gi]
            best = None
            for rot, shape in shapes:
                if shape.cell_count > gfree:
                    continue
                pos = grid.find_position(shape)
                if pos is None:
                    continue
                score = (pos[1] + shape.height, pos[0])
                if best is None or score < best[0]:
                    best = (score, rot, shape, pos)
            if best is None:
                continue
            _, rot, shape, (x, y) = best
            grid.place(shape, x, y)
            if gi is None:
                grids.append(grid)
                sheets.append([])
                free.append(gfree)
                gi = len(grids) - 1
            free[gi] -= shape.cell_count
            sox, soy = shape.origin
            sheets[gi].append((part, rot,
                               ctx.origin + x * ctx.cell - sox,
                               ctx.origin + y * ctx.cell - soy))
            placed = True
            break
        if not placed:
            unplaced.append(part)
    return sheets, unplaced


def _score(ctx, sheets, unplaced):
    tops = []
    for items in sheets:
        top = 0.0
        for part, rot, tx, ty in items:
            top = max(top, ctx.geo(part, rot)[2][3] + ty)
        tops.append(top)
    last = tops[-1] if tops else 0.0
    return (len(unplaced), len(sheets), round(last, 6), round(sum(tops), 6))


# --------------------------------------------------------------------------
# Search driver
# --------------------------------------------------------------------------

def _base_orders(ctx, parts):
    def dims(p):
        bx0, by0, bx1, by1 = ctx.geo(p, ctx.rotations[0])[2]
        return bx1 - bx0, by1 - by0

    def area(p):
        if p.area > 0:
            return p.area
        w, h = dims(p)
        return w * h

    keys = [
        lambda p: -area(p),
        lambda p: -max(dims(p)),
        lambda p: -dims(p)[1],
        lambda p: -dims(p)[0],
        lambda p: -sum(dims(p)),
    ]
    orders = []
    for k in keys:
        o = sorted(parts, key=lambda p, k=k: (k(p), p.key, p.name))
        if o not in orders:
            orders.append(o)
    return orders, area


def nest(parts, opt):
    """Nest `parts` (list of NestPart) onto as few sheets as possible."""
    ctx = _Context(parts, opt)
    rng = random.Random(opt.seed)
    start = time.perf_counter()
    budget = max(opt.time_budget, 0.0)
    method = opt.method if opt.method in ('best', 'rect', 'shape') else 'best'
    state = {'best': None, 'attempts': 0, 'cancelled': False}

    def elapsed_fraction():
        return (time.perf_counter() - start) / budget if budget > 0 else 1.0

    def report(frac, message):
        if opt.progress is not None and not opt.progress(min(max(frac, 0.0), 1.0), message):
            state['cancelled'] = True
            raise _Cancelled()

    def consider(sheets, unplaced, engine):
        state['attempts'] += 1
        score = _score(ctx, sheets, unplaced)
        if state['best'] is None or score < state['best'][0]:
            state['best'] = (score, sheets, unplaced, engine)

    if not parts:
        return NestResult([], [], method, 0, ctx.length, ctx.width)

    orders, area = _base_orders(ctx, parts)
    rect_share = {'rect': 1.0, 'shape': 0.0, 'best': 0.25}[method]

    try:
        # Rectangle engine: deterministic orders x rules, then randomised orders.
        if method in ('rect', 'best'):
            for order in orders:
                for rule in ('bl', 'bssf', 'baf'):
                    consider(*_run_rect(ctx, order, rule), 'rect')
            report(elapsed_fraction(), 'Packing rectangles')
            while elapsed_fraction() < rect_share and state['attempts'] < 400:
                order = _noisy_order(parts, area, rng)
                consider(*_run_rect(ctx, order, rng.choice(('bl', 'bssf', 'baf'))), 'rect')
                report(elapsed_fraction(), 'Packing rectangles')

        # Shape engine: the first attempt always runs to completion.
        if method in ('shape', 'best'):
            shape_orders = list(orders[:2])
            first = True
            while first or elapsed_fraction() < 1.0:
                order = shape_orders.pop(0) if shape_orders else _noisy_order(parts, area, rng)
                message = 'Nesting true shapes (attempt %d)' % (state['attempts'] + 1)
                consider(*_run_shape(ctx, order, lambda f: report(elapsed_fraction(), message)), 'shape')
                first = False
    except _Cancelled:
        pass

    if state['best'] is None:
        result = NestResult([], list(parts), method, state['attempts'], ctx.length, ctx.width, True)
        return result

    _, sheets_alg, unplaced, engine = state['best']
    sheet_area = ctx.length * ctx.width
    layouts = []
    for i, items in enumerate(sheets_alg):
        layout = SheetLayout(i + 1)
        used = 0.0
        for part, rot, tx, ty in items:
            layout.placements.append(ctx.to_placement(part, rot, tx, ty))
            used += area(part)
        layout.utilization = used / sheet_area if sheet_area > 0 else 0.0
        layouts.append(layout)
    return NestResult(layouts, unplaced, engine, state['attempts'],
                      ctx.length, ctx.width, state['cancelled'])


def _noisy_order(parts, area, rng):
    return sorted(parts, key=lambda p: -area(p) * rng.uniform(0.7, 1.3))
