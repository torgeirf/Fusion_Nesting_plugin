"""Turning Fusion bodies into flat parts for the nesting engine."""

import math

import adsk.core
import adsk.fusion

from .nestcore import geometry
from .nestcore.nester import NestPart

ATTR_GROUP = 'SheetNester'
ATTR_LAYOUT = 'layout'

_ANGLE_TOL = math.cos(math.radians(1.0))
_PLANE_TOL = 1e-4      # cm, for "face lies on the top/bottom plane"
THICKNESS_TOL = 0.005  # cm (0.05 mm), for grouping parts by thickness


class PartInfo:
    """A body laid flat: base frame, outline and the data needed to place it."""

    def __init__(self):
        self.name = ''
        self.body = None        # source body (proxy if inside an occurrence)
        self.temp = None        # temporary copy in world coordinates
        self.thickness = 0.0
        self.basis = None       # (u, v, w) unit vectors; w points up from the sheet
        self.zmin = 0.0         # w . p of the face resting on the sheet
        self.angle = 0.0        # in-plane rotation applied to the base frame
        self.outlines = []
        self.polys = []
        self.area = 0.0
        self.size = (0.0, 0.0)  # length x width of the flat part
        self.warnings = []

    def nest_part(self, copy_index):
        return NestPart(key=self.key, name=self.label(copy_index), polys=self.polys,
                        outlines=self.outlines, area=self.area, data=(self, copy_index))

    @property
    def key(self):
        return str(id(self))

    def label(self, copy_index):
        return self.name if copy_index == 0 else '%s (%d)' % (self.name, copy_index + 1)

    def matrix(self, rotation, tx, ty):
        """Matrix3D taking the world body to its spot on the sheet."""
        a = math.radians(self.angle + rotation)
        c, s = math.cos(a), math.sin(a)
        u, v, w = self.basis
        rows = [
            [c * u[i] - s * v[i] for i in range(3)] + [tx],
            [s * u[i] + c * v[i] for i in range(3)] + [ty],
            [w[0], w[1], w[2], -self.zmin],
        ]
        m = adsk.core.Matrix3D.create()
        for r in range(3):
            for col in range(4):
                m.setCell(r, col, rows[r][col])
        return m


# ------------------------------------------------------------------ helpers

def _t(v):
    return (v.x, v.y, v.z)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _canonical(n):
    """Direction with a fixed sign so that n and -n map to the same axis."""
    k = max(range(3), key=lambda i: abs(n[i]))
    return n if n[k] > 0 else (-n[0], -n[1], -n[2])


def is_layout_body(body: adsk.fusion.BRepBody):
    occ = body.assemblyContext
    while occ:
        if occ.component.attributes.itemByName(ATTR_GROUP, ATTR_LAYOUT):
            return True
        occ = occ.assemblyContext
    return False


def all_design_bodies(design: adsk.fusion.Design):
    """Every visible solid body in the design (as proxies where needed)."""
    root = design.rootComponent
    bodies = [b for b in root.bRepBodies]
    for occ in root.allOccurrences:
        bodies.extend(b for b in occ.bRepBodies)
    return [b for b in bodies if b.isSolid and b.isVisible and not is_layout_body(b)]


def collect_bodies(entities):
    """Solid bodies from a mix of selected bodies and occurrences, de-duplicated."""
    out, seen = [], set()

    def add(body):
        if not body.isSolid or is_layout_body(body):
            return
        token = body.entityToken
        if token not in seen:
            seen.add(token)
            out.append(body)

    def walk(occ):
        for b in occ.bRepBodies:
            if b.isVisible:
                add(b)
        for child in occ.childOccurrences:
            if child.isVisible:
                walk(child)

    for e in entities:
        body = adsk.fusion.BRepBody.cast(e)
        if body:
            add(body)
            continue
        occ = adsk.fusion.Occurrence.cast(e)
        if occ:
            walk(occ)
    return out


def part_name(body: adsk.fusion.BRepBody):
    comp = body.parentComponent
    if body.assemblyContext and comp.bRepBodies.count == 1:
        return comp.name
    if body.assemblyContext:
        return '%s / %s' % (comp.name, body.name)
    return body.name


# ------------------------------------------------------------- orientation

def panel_axis(body: adsk.fusion.BRepBody, largest_down=True):
    """Find how a body lies flat.

    Returns (w, thickness, zmin) where w is the unit "up" vector once the part
    lies on the sheet, or None if the body has no usable orientation.

    The thickness axis is the direction shared by the largest total area of
    parallel planar faces. The part is turned so the larger of the two
    outermost faces rests on the sheet, which leaves chamfers and bevels facing
    up where a V-bit or chamfer bit can reach them."""
    faces = []
    for face in body.faces:
        if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            continue
        p = face.pointOnFace
        ok, n = face.evaluator.getNormalAtPoint(p)
        if not ok:
            continue
        faces.append((_unit(_t(n)), _t(p), face.area))

    clusters = []  # [axis, total area]
    for n, p, area in faces:
        c = _canonical(n)
        for cl in clusters:
            if abs(_dot(cl[0], c)) > _ANGLE_TOL:
                cl[1] += area
                break
        else:
            clusters.append([c, area])

    if clusters:
        axis = max(clusters, key=lambda cl: cl[1])[0]
        members = [(n, _dot(axis, p), area) for n, p, area in faces if abs(_dot(axis, n)) > _ANGLE_TOL]
        lo = min(o for _, o, _ in members)
        hi = max(o for _, o, _ in members)
        if hi - lo > _PLANE_TOL:
            top_area = sum(a for n, o, a in members if _dot(axis, n) > 0 and o > hi - _PLANE_TOL)
            bottom_area = sum(a for n, o, a in members if _dot(axis, n) < 0 and o < lo + _PLANE_TOL)
            up = axis if bottom_area >= top_area else (-axis[0], -axis[1], -axis[2])
            if not largest_down:
                up = (-up[0], -up[1], -up[2])
            zmin = lo if up == axis else -hi
            return up, hi - lo, zmin

    # No pair of parallel faces: fall back to the thinnest side of the
    # minimum bounding box.
    try:
        obb = body.orientedMinimumBoundingBox
        dims = [(obb.length, obb.lengthDirection), (obb.width, obb.widthDirection),
                (obb.height, obb.heightDirection)]
        size, direction = min(dims, key=lambda d: d[0])
        up = _unit(_t(direction))
        return up, size, _dot(up, _t(obb.centerPoint)) - size / 2.0
    except Exception:
        return None


def thickness_of(body: adsk.fusion.BRepBody):
    res = panel_axis(body)
    return res[1] if res else None


# ----------------------------------------------------------------- analysis

def analyze(body: adsk.fusion.BRepBody, largest_down=True, tolerance=0.005):
    """Build a PartInfo for `body`. Raises ValueError for unusable bodies."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    info = PartInfo()
    info.body = body
    info.name = part_name(body)

    temp = tbm.copy(body)
    if temp is None:
        raise ValueError('could not copy the body')
    _align_to_world(temp, body)
    info.temp = temp

    res = panel_axis(temp, largest_down)
    if res is None:
        raise ValueError('could not work out how the part lies flat')
    w, info.thickness, info.zmin = res
    ref = (0.0, 0.0, 1.0) if abs(w[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(_cross(ref, w))
    v = _cross(w, u)
    info.basis = (u, v, w)

    outlines, polys, area = _outline(temp, info.basis, tolerance)
    if not outlines and not polys:
        raise ValueError('could not compute the flat outline')
    if not outlines:
        info.warnings.append('outline approximated from the mesh')

    pts = [p for line in outlines for p in line] + [p for poly in polys for p in poly]
    info.angle = geometry.min_area_rect_angle(pts)
    info.outlines = [geometry.rotate_points(line, info.angle) for line in outlines]
    info.polys = [geometry.rotate_points(poly, info.angle) for poly in polys]
    x0, y0, x1, y1 = geometry.bbox([p for line in info.outlines for p in line] +
                                   [p for poly in info.polys for p in poly])
    info.size = (x1 - x0, y1 - y0)
    info.area = area if area > 0 else (x1 - x0) * (y1 - y0)
    return info


def _align_to_world(temp: adsk.fusion.BRepBody, body: adsk.fusion.BRepBody):
    """Make sure a temporary copy of a proxy body sits where the user sees it."""
    occ = body.assemblyContext
    if occ is None:
        return
    a, b = body.boundingBox, temp.boundingBox
    if a.minPoint.distanceTo(b.minPoint) < 1e-6 and a.maxPoint.distanceTo(b.maxPoint) < 1e-6:
        return
    adsk.fusion.TemporaryBRepManager.get().transform(temp, occ.transform2)


def _outline(temp: adsk.fusion.BRepBody, basis, tolerance):
    """Silhouette of the body looking down -w, in (u, v) coordinates.

    Returns (outlines, polys, area). Outlines come from Fusion's projected body
    outline; if that fails, the projected triangles of a mesh are used."""
    u, v, w = basis
    tbm = adsk.fusion.TemporaryBRepManager.get()

    def uv(p):
        q = (p.x, p.y, p.z)
        return (_dot(u, q), _dot(v, q))

    try:
        plane = adsk.core.Plane.create(adsk.core.Point3D.create(0, 0, 0), adsk.core.Vector3D.create(*w))
        result = tbm.createProjectedBodyOutline(temp, plane, tolerance)
        outline_body = result[0] if isinstance(result, (tuple, list)) else result
        if outline_body and outline_body.edges.count:
            lines = []
            for edge in outline_body.edges:
                ev = edge.evaluator
                ok, t0, t1 = ev.getParameterExtents()
                if not ok:
                    continue
                ok, pts = ev.getStrokes(t0, t1, tolerance)
                if ok and len(pts) >= 2:
                    lines.append([uv(p) for p in pts])
            area = sum(f.area for f in outline_body.faces)
            if lines:
                return lines, [], area
    except Exception:
        pass

    try:
        calc = temp.meshManager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.NormalQualityTriangleMesh)
        calc.surfaceTolerance = tolerance
        mesh = calc.calculate()
        coords = mesh.nodeCoordinatesAsDouble
        idx = mesh.nodeIndices
        pts = [(_dot(u, coords[i:i + 3]), _dot(v, coords[i:i + 3])) for i in range(0, len(coords), 3)]
        polys = []
        for i in range(0, len(idx), 3):
            tri = [pts[idx[i]], pts[idx[i + 1]], pts[idx[i + 2]]]
            if abs(geometry.triangle_area(*tri)) > 1e-10:
                polys.append(tri)
        if polys:
            return [], polys, 0.0
    except Exception:
        pass

    pts = [uv(vx.geometry) for vx in temp.vertices]
    hull = geometry.convex_hull(pts)
    return [], ([hull] if len(hull) >= 3 else []), 0.0
