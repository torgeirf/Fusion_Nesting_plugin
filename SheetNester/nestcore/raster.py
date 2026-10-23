"""Bitmap ("raster") collision model for true-shape nesting.

A shape is stored as a list of Python ints, one per grid row, where bit k is
set when grid column k is occupied. Python's arbitrary precision ints make the
row operations (shift, or, and) fast enough for whole-sheet searches.

Rasterisation is conservative: every grid cell that touches the shape is
marked, so two shapes whose cell sets do not overlap are guaranteed not to
overlap in reality.
"""

import math

from .geometry import convex_band_xrange

_EPS = 1e-9


def smear(mask, n):
    """OR of mask >> t for t in range(n) (n >= 1)."""
    result = mask
    covered = 1
    while covered * 2 <= n:
        result |= result >> covered
        covered *= 2
    if covered < n:
        result |= result >> (n - covered)
    return result


def row_spans(row):
    """Runs of set bits in `row` as (start, end) inclusive column pairs."""
    spans = []
    k = 0
    while row:
        if row & 1:
            # Count the run of ones starting here.
            run = (~row & (row + 1)).bit_length() - 1
            spans.append((k, k + run - 1))
            row >>= run
            k += run
        else:
            zeros = (row & -row).bit_length() - 1
            row >>= zeros
            k += zeros
    return spans


class ShapeRaster:
    """Conservative raster of a shape, dilated by `radius`.

    The shape is the union of the convex polygons in `polys` and the regions
    enclosed by `outlines` (polylines whose segments together form closed
    boundaries, filled with the even-odd rule so holes stay open).

    `origin` is the model-space position of the lower-left corner of cell
    (0, 0)."""

    def __init__(self, polys, cell, radius, outlines=()):
        self.cell = cell
        rows, ox, oy = _rasterize(polys, outlines, cell)
        if radius > 0:
            rows, ox, oy = _dilate(rows, ox, oy, cell, radius)
        # Trim empty rows/columns so width/height are tight.
        while rows and rows[-1] == 0:
            rows.pop()
        lead = 0
        while lead < len(rows) and rows[lead] == 0:
            lead += 1
        if lead:
            rows = rows[lead:]
            oy += lead * cell
        low = min(((r & -r).bit_length() - 1) for r in rows if r)
        if low:
            rows = [r >> low for r in rows]
            ox += low * cell
        self.rows = rows
        self.origin = (ox, oy)
        self.height = len(rows)
        self.width = max(r.bit_length() for r in rows)
        self.cell_count = sum(bin(r).count('1') for r in rows)
        self.groups = _row_groups(rows)


def _rasterize(polys, outlines, cell):
    segments = []
    for line in outlines:
        for i in range(len(line) - 1):
            segments.append((line[i], line[i + 1]))
    pts = [p for poly in polys for p in poly] + [p for line in outlines for p in line]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ox, oy = min(xs), min(ys)
    ncols = max(1, int(math.ceil((max(xs) - ox) / cell - _EPS)))
    nrows = max(1, int(math.ceil((max(ys) - oy) / cell - _EPS)))
    rows = [0] * nrows

    if segments:
        # Interior: cells whose centre is inside (even-odd). Cells that are only
        # partly inside touch the boundary and are caught by the segment pass.
        for r in range(nrows):
            yc = oy + (r + 0.5) * cell
            crossings = []
            for (ax, ay), (bx, by) in segments:
                if (ay <= yc < by) or (by <= yc < ay):
                    crossings.append(ax + (yc - ay) * (bx - ax) / (by - ay))
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                k0 = max(0, int(math.ceil((crossings[i] - ox) / cell - 0.5)))
                k1 = min(ncols - 1, int(math.floor((crossings[i + 1] - ox) / cell - 0.5)))
                if k1 >= k0:
                    rows[r] |= ((1 << (k1 - k0 + 1)) - 1) << k0

    for poly in list(polys) + [list(s) for s in segments]:
        pys = [p[1] for p in poly]
        r0 = max(0, int(math.floor((min(pys) - oy) / cell + _EPS)))
        r1 = min(nrows - 1, max(r0, int(math.ceil((max(pys) - oy) / cell - _EPS)) - 1))
        for r in range(r0, r1 + 1):
            y0 = oy + r * cell
            xr = convex_band_xrange(poly, y0, y0 + cell)
            if xr is None:
                continue
            k0 = max(0, int(math.floor((xr[0] - ox) / cell + _EPS)))
            k1 = min(ncols - 1, max(k0, int(math.ceil((xr[1] - ox) / cell - _EPS)) - 1))
            rows[r] |= ((1 << (k1 - k0 + 1)) - 1) << k0
    return rows, ox, oy


def _dilate(rows, ox, oy, cell, radius):
    """Grow the cell set so it covers every cell within `radius` of the
    original shape. Cell-to-cell distance is measured between the closest
    points of the two squares, which keeps the result conservative."""
    rc = radius / cell
    reach = int(math.floor(rc + _EPS)) + 1  # max row/column offset
    half_widths = []
    for dj in range(reach + 1):
        gap = max(dj - 1, 0)
        if gap > rc + _EPS:
            break
        half_widths.append(int(math.floor(math.sqrt(max(rc * rc - gap * gap, 0.0)) + _EPS)) + 1)
    rr = len(half_widths) - 1          # row reach actually used
    wmax = half_widths[0]
    out = [0] * (len(rows) + 2 * rr)
    cache = {}
    for y, row in enumerate(rows):
        if not row:
            continue
        for dj in range(-rr, rr + 1):
            w = half_widths[abs(dj)]
            key = (y, w)
            grown = cache.get(key)
            if grown is None:
                grown = smear(row << (wmax + w), 2 * w + 1)
                cache[key] = grown
            out[y + rr + dj] |= grown
    return out, ox - wmax * cell, oy - rr * cell


def _row_groups(rows):
    """Group consecutive identical rows -> list of (first, last, spans), the
    widest groups first so that collision checks fail fast."""
    groups = []
    start = 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i] != rows[start]:
            if rows[start]:
                groups.append((start, i - 1, row_spans(rows[start]), bin(rows[start]).count('1')))
            start = i
    groups.sort(key=lambda g: -g[3])
    return [(g[0], g[1], g[2]) for g in groups]


class RasterSheet:
    """Occupancy grid of one sheet's usable area."""

    def __init__(self, ncols, nrows):
        self.ncols = ncols
        self.nrows = nrows
        self.rows = [0] * nrows
        self._table = None

    def _range_table(self):
        if self._table is None:
            table = [self.rows]
            span = 1
            while span * 2 <= self.nrows:
                prev = table[-1]
                table.append([prev[i] | prev[i + span] for i in range(self.nrows - 2 * span + 1)])
                span *= 2
            self._table = table
        return self._table

    def range_or(self, r0, r1):
        table = self._range_table()
        level = (r1 - r0 + 1).bit_length() - 1
        t = table[level]
        return t[r0] | t[r1 - (1 << level) + 1]

    def find_position(self, shape):
        """Lowest (y, then x) cell position where `shape` fits, or None."""
        if shape.width > self.ncols or shape.height > self.nrows:
            return None
        valid = (1 << (self.ncols - shape.width + 1)) - 1
        groups = shape.groups
        for y in range(0, self.nrows - shape.height + 1):
            forbidden = 0
            for r0, r1, spans in groups:
                occupied = self.range_or(y + r0, y + r1)
                if not occupied:
                    continue
                for a, b in spans:
                    forbidden |= smear(occupied >> a, b - a + 1)
                if forbidden & valid == valid:
                    break
            else:
                free = valid & ~forbidden
                if free:
                    return (free & -free).bit_length() - 1, y
        return None

    def place(self, shape, x, y):
        for r, row in enumerate(shape.rows):
            if row:
                self.rows[y + r] |= row << x
        self._table = None
