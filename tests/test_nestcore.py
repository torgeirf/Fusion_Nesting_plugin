"""Tests for the pure-Python nesting engine. Run: python -m unittest discover tests"""

import math
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'SheetNester'))

from nestcore import NestOptions, NestPart, nest  # noqa: E402
from nestcore import geometry  # noqa: E402
from nestcore.raster import RasterSheet, ShapeRaster, row_spans, smear  # noqa: E402


# ---------------------------------------------------------------- shapes (cm)

def rect(w, h):
    return [[(0, 0), (w, 0), (w, h)], [(0, 0), (w, h), (0, h)]]


def trapezoid(bottom, top, h):
    off = (bottom - top) / 2.0
    return [[(0, 0), (bottom, 0), (bottom - off, h), (off, h)]]


def circle_pts(cx, cy, r, n=64):
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


def annulus(r_out, r_in):
    """Ring given as outlines (outer + hole, even-odd filled)."""
    return [circle_pts(0, 0, r_out), circle_pts(0, 0, r_in)]


def baffle(w, h, hole_r, hole_y):
    """Rectangle with a round driver hole, given as outlines."""
    return [[(0, 0), (w, 0), (w, h), (0, h), (0, 0)], circle_pts(w / 2.0, hole_y, hole_r)]


# ------------------------------------------------------- geometric checking

def transform(part, pl):
    """(convex polys, outlines) of a placed part in sheet coordinates."""
    def move(pts):
        return [(x + pl.x, y + pl.y) for x, y in geometry.rotate_points(pts, pl.rotation)]
    return [move(p) for p in part.polys], [move(o) for o in part.outlines]


def pieces(shape):
    """Convex pieces whose distances bound the shape's boundary distance."""
    polys, outlines = shape
    return polys + [[o[i], o[i + 1]] for o in outlines for i in range(len(o) - 1)]


def in_material(pt, shape):
    polys, outlines = shape
    for P in polys:
        n = len(P)
        signs = [geometry.triangle_area(P[i], P[(i + 1) % n], pt) for i in range(n)]
        if all(v >= -1e-12 for v in signs) or all(v <= 1e-12 for v in signs):
            return True
    inside = False
    for o in outlines:
        for (ax, ay), (bx, by) in zip(o, o[1:]):
            if (ay <= pt[1] < by) or (by <= pt[1] < ay):
                if ax + (pt[1] - ay) * (bx - ax) / (by - ay) > pt[0]:
                    inside = not inside
    return inside


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _separated(P, Q):
    axes = []
    for poly in (P, Q):
        n = len(poly)
        for i in range(n):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            axes.append((by - ay, ax - bx))
            if n == 2:
                # A segment also needs its own direction as a candidate axis.
                axes.append((bx - ax, by - ay))
    for nx, ny in axes:
        if nx == 0 and ny == 0:
            continue
        pp = [nx * x + ny * y for x, y in P]
        qq = [nx * x + ny * y for x, y in Q]
        if max(pp) < min(qq) or max(qq) < min(pp):
            return True
    return False


def convex_distance(P, Q):
    if not _separated(P, Q):
        return 0.0
    d = math.inf
    for A, B in ((P, Q), (Q, P)):
        for p in A:
            for i in range(len(B)):
                d = min(d, _seg_dist(p, B[i], B[(i + 1) % len(B)]))
    return d


def min_distance(polys_a, polys_b):
    best = math.inf
    for P in polys_a:
        bp = geometry.bbox(P)
        for Q in polys_b:
            bq = geometry.bbox(Q)
            gap = max(bq[0] - bp[2], bp[0] - bq[2], bq[1] - bp[3], bp[1] - bq[3], 0)
            if gap >= best:
                continue
            best = min(best, convex_distance(P, Q))
    return best


class LayoutChecks:
    def check_layout(self, result, opt, tol=1e-6):
        L = max(opt.sheet_length, opt.sheet_width)
        W = min(opt.sheet_length, opt.sheet_width)
        for sheet in result.sheets:
            placed = [(pl, transform(pl.part, pl)) for pl in sheet.placements]
            for pl, shape in placed:
                pts = [p for piece in pieces(shape) for p in piece]
                xs = [x for x, _ in pts]
                ys = [y for _, y in pts]
                self.assertGreaterEqual(min(xs), opt.margin - tol, pl.part.name)
                self.assertGreaterEqual(min(ys), opt.margin - tol, pl.part.name)
                self.assertLessEqual(max(xs), L - opt.margin + tol, pl.part.name)
                self.assertLessEqual(max(ys), W - opt.margin + tol, pl.part.name)
                self.assertAlmostEqual(min(xs), pl.bbox[0], places=6)
                self.assertAlmostEqual(max(ys), pl.bbox[3], places=6)
            for i in range(len(placed)):
                for j in range(i + 1, len(placed)):
                    a, b = placed[i][1], placed[j][1]
                    d = min_distance(pieces(a), pieces(b))
                    self.assertFalse(in_material(pieces(a)[0][0], b) or in_material(pieces(b)[0][0], a),
                                     '%s / %s overlap' % (placed[i][0].part.name, placed[j][0].part.name))
                    self.assertGreaterEqual(d, opt.spacing - tol,
                                            '%s / %s too close: %.4f' % (placed[i][0].part.name,
                                                                         placed[j][0].part.name, d))


def speaker_parts(copies=1):
    spec = [('Side', 40, 30, 2), ('Top/Bottom', 30, 25, 2), ('Back', 40, 25, 1), ('Brace', 25, 8, 2)]
    parts = []
    for c in range(copies):
        for name, w, h, n in spec:
            for k in range(n):
                parts.append(NestPart(name, '%s %d.%d' % (name, c, k), rect(w, h), area=w * h))
        parts.append(NestPart('Baffle', 'Baffle %d' % c, outlines=baffle(25, 40, 8, 25), area=25 * 40 - math.pi * 64))
    return parts


# --------------------------------------------------------------------- tests

class GeometryTests(unittest.TestCase):
    def test_min_area_rect_angle_aligns_rotated_rectangle(self):
        pts = geometry.rotate_points([(0, 0), (50, 0), (50, 20), (0, 20)], 30)
        ang = geometry.min_area_rect_angle(pts)
        x0, y0, x1, y1 = geometry.bbox(geometry.rotate_points(pts, ang))
        self.assertAlmostEqual(x1 - x0, 50, places=6)
        self.assertAlmostEqual(y1 - y0, 20, places=6)

    def test_axis_aligned_rectangle_is_not_rotated(self):
        self.assertAlmostEqual(geometry.min_area_rect_angle([(0, 0), (50, 0), (50, 20), (0, 20)]), 0.0)

    def test_band_xrange(self):
        tri = [(0, 0), (10, 0), (0, 10)]
        self.assertEqual(geometry.convex_band_xrange(tri, 0, 5), (0, 10))
        self.assertEqual(geometry.convex_band_xrange(tri, 5, 6), (0, 5))
        self.assertIsNone(geometry.convex_band_xrange(tri, 11, 12))
        self.assertEqual(geometry.convex_band_xrange([(1, 1), (4, 1)], 0, 2), (1, 4))


class RasterTests(unittest.TestCase):
    def test_smear_and_spans(self):
        self.assertEqual(smear(0b100000, 3), 0b111000)
        self.assertEqual(smear(0b1, 1), 0b1)
        self.assertEqual(row_spans(0b1110011), [(0, 1), (4, 6)])

    def test_rectangle_raster_is_tight(self):
        s = ShapeRaster(rect(10, 5), 1.0, 0.0)
        self.assertEqual((s.width, s.height), (10, 5))
        self.assertEqual(s.cell_count, 50)

    def test_outline_with_hole(self):
        s = ShapeRaster([], 1.0, 0.0, [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
                                       [(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]])
        self.assertEqual((s.width, s.height), (10, 10))
        self.assertFalse(s.rows[5] >> 5 & 1, 'hole centre must stay open')
        self.assertTrue(s.rows[0] & 1 and s.rows[9] >> 9 & 1)
        self.assertGreaterEqual(s.cell_count, 100 - 16)
        self.assertLessEqual(s.cell_count, 100 - 4)

    def test_dilation_covers_radius(self):
        s = ShapeRaster(rect(10, 10), 1.0, 2.0)
        # 10 cells + (floor(2/1)+1) guard cells each side.
        self.assertEqual(s.width, 16)
        self.assertEqual(s.height, 16)

    def test_sheet_finds_free_space(self):
        sheet = RasterSheet(20, 10)
        s = ShapeRaster(rect(10, 10), 1.0, 0.0)
        self.assertEqual(sheet.find_position(s), (0, 0))
        sheet.place(s, 0, 0)
        self.assertEqual(sheet.find_position(s), (10, 0))
        sheet.place(s, 10, 0)
        self.assertIsNone(sheet.find_position(s))


class NestTests(LayoutChecks, unittest.TestCase):
    def opts(self, **kw):
        base = dict(sheet_length=244.0, sheet_width=122.0, margin=1.0, spacing=0.6, time_budget=1.0)
        base.update(kw)
        return NestOptions(**base)

    def test_rect_engine_speaker(self):
        opt = self.opts(method='rect')
        res = nest(speaker_parts(), opt)
        self.assertEqual(len(res.sheets), 1)
        self.assertFalse(res.unplaced)
        self.check_layout(res, opt)

    def test_shape_engine_speaker(self):
        opt = self.opts(method='shape')
        res = nest(speaker_parts(), opt)
        self.assertEqual(len(res.sheets), 1)
        self.assertFalse(res.unplaced)
        self.check_layout(res, opt)

    def test_short_side_fill_keeps_parts_at_one_end(self):
        for method in ('rect', 'shape'):
            long_opt = self.opts(method=method, fill='long')
            short_opt = self.opts(method=method, fill='short')
            parts = speaker_parts()
            res_long = nest(parts, long_opt)
            res_short = nest(parts, short_opt)
            self.check_layout(res_long, long_opt)
            self.check_layout(res_short, short_opt)
            max_x = max(p.bbox[2] for p in res_short.sheets[0].placements)
            max_y = max(p.bbox[3] for p in res_long.sheets[0].placements)
            self.assertLess(max_x, 244 * 0.75, method)   # offcut left at the far end
            self.assertLess(max_y, 122 * 0.9, method)    # offcut strip along the long edge

    def test_multiple_sheets_and_oversize(self):
        parts = speaker_parts(copies=4) + [NestPart('Huge', 'Huge', rect(300, 50), area=15000)]
        opt = self.opts(method='best', time_budget=2.0)
        res = nest(parts, opt)
        self.assertEqual([p.name for p in res.unplaced], ['Huge'])
        self.assertGreaterEqual(len(res.sheets), 1)
        self.assertEqual(sum(len(s.placements) for s in res.sheets), len(parts) - 1)
        self.check_layout(res, opt)

    def test_trapezoids_interlock_with_shape_engine(self):
        # 20 trapezoids 60 wide at the bottom, 20 at the top. As rectangles they
        # need 60 x 30 each; flipped 180 degrees they nest into each other.
        parts = [NestPart('T', 'T%d' % i, trapezoid(60, 20, 30), area=30 * 40) for i in range(20)]
        opt_shape = self.opts(method='shape', time_budget=2.0)
        opt_rect = self.opts(method='rect')
        res_shape = nest(parts, opt_shape)
        res_rect = nest(parts, opt_rect)
        self.check_layout(res_shape, opt_shape)
        on_first = len(res_shape.sheets[0].placements)
        print('\n  trapezoids on first sheet: shape %d, rect %d' % (on_first, len(res_rect.sheets[0].placements)))
        self.assertEqual(len(res_rect.sheets[0].placements), 12)
        self.assertGreaterEqual(on_first, 15)

    def test_small_part_goes_into_large_hole(self):
        parts = [NestPart('Ring', 'Ring', outlines=annulus(55, 40), area=1),
                 NestPart('Disc', 'Disc', outlines=[circle_pts(0, 0, 30)], area=1)]
        opt = NestOptions(sheet_length=115, sheet_width=114, margin=1, spacing=0.6, method='shape',
                          rotations=(0,), time_budget=0.5)
        res = nest(parts, opt)
        self.assertEqual(len(res.sheets), 1, 'disc should fit inside the ring')
        self.check_layout(res, opt)

    def test_rotation_lock(self):
        parts = [NestPart('Long', 'Long%d' % i, rect(100, 10), area=1000) for i in range(3)]
        opt = self.opts(method='best', rotations=(0, 180))
        res = nest(parts, opt)
        for pl in res.sheets[0].placements:
            self.assertIn(pl.rotation, (0.0, 180.0))
            self.assertAlmostEqual(pl.bbox[2] - pl.bbox[0], 100, places=6)
        self.check_layout(res, opt)

    def test_cancel_returns_rect_result(self):
        calls = []

        def progress(frac, msg):
            calls.append(msg)
            return 'true shapes' not in msg

        res = nest(speaker_parts(), self.opts(method='best', progress=progress))
        self.assertTrue(res.cancelled)
        self.assertEqual(res.method, 'rect')
        self.assertEqual(len(res.sheets), 1)


class Benchmark(unittest.TestCase):
    def test_speed_of_first_shape_attempt(self):
        parts = speaker_parts(copies=3)
        opt = NestOptions(sheet_length=244, sheet_width=122, margin=1, spacing=0.6, method='shape',
                          time_budget=0.0)
        t = time.perf_counter()
        res = nest(parts, opt)
        dt = time.perf_counter() - t
        print('\n  shape engine, %d parts -> %d sheets, first attempt %.2fs' % (len(parts), len(res.sheets), dt))
        self.assertLess(dt, 15.0)


if __name__ == '__main__':
    unittest.main()
