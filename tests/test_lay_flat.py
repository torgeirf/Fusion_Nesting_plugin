"""Checks the lay-flat orientation and placement matrix with a fake Fusion API.

A chamfered panel is modelled as a set of planar faces plus vertices, tilted
arbitrarily in space. After analysis and nesting, applying the placement
matrix to the vertices must put the part flat on the sheet (z from 0 to the
thickness, larger face down) and inside the bounding box the nester reported.
"""

import math
import os
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)


# ------------------------------------------------------------ fake adsk API

class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Matrix:
    def __init__(self):
        self.m = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]

    @staticmethod
    def create():
        return _Matrix()

    def setCell(self, r, c, v):
        self.m[r][c] = v
        return True

    def apply(self, p):
        return tuple(sum(self.m[r][c] * p[c] for c in range(3)) + self.m[r][3] for r in range(3))


class _Face:
    def __init__(self, normal, point, area):
        self.geometry = types.SimpleNamespace(surfaceType=0)
        self.pointOnFace = _Vec(*point)
        self.area = area
        n = _Vec(*normal)
        self.evaluator = types.SimpleNamespace(getNormalAtPoint=lambda p: (True, n))


class _Body:
    def __init__(self, faces, vertices):
        self.faces = faces
        self.vertices = [types.SimpleNamespace(geometry=_Vec(*v)) for v in vertices]
        self.assemblyContext = None
        self.parentComponent = types.SimpleNamespace(name='Comp', bRepBodies=types.SimpleNamespace(count=1))
        self.name = 'Panel'

    @property
    def meshManager(self):
        raise RuntimeError('no mesh in the fake API')


class _TBM:
    @staticmethod
    def get():
        return _TBM()

    def copy(self, body):
        return body

    def createProjectedBodyOutline(self, *a):
        raise RuntimeError('not available in the fake API')


def _install_fake_adsk():
    adsk = types.ModuleType('adsk')
    core = types.ModuleType('adsk.core')
    fusion = types.ModuleType('adsk.fusion')
    core.Matrix3D = _Matrix
    core.SurfaceTypes = types.SimpleNamespace(PlaneSurfaceType=0)
    core.Point3D = types.SimpleNamespace(create=lambda *a: _Vec(*a))
    core.Vector3D = types.SimpleNamespace(create=lambda *a: _Vec(*a))
    core.Plane = types.SimpleNamespace(create=lambda *a: None)
    fusion.TemporaryBRepManager = _TBM
    fusion.TriangleMeshQualityOptions = types.SimpleNamespace(NormalQualityTriangleMesh=11)
    # Anything else (used only in type annotations) resolves to a dummy class.
    for mod in (core, fusion):
        mod.__getattr__ = lambda name: type(name, (), {})
    adsk.core, adsk.fusion = core, fusion
    sys.modules.update({'adsk': adsk, 'adsk.core': core, 'adsk.fusion': fusion})


_install_fake_adsk()
from SheetNester import sn_parts  # noqa: E402
from SheetNester.nestcore import NestOptions, nest  # noqa: E402


# ------------------------------------------------------------- test panel

def _rot(axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = axis
    return [[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]]


def _apply(R, p, t=(0, 0, 0)):
    return tuple(sum(R[r][c] * p[c] for c in range(3)) + t[r] for r in range(3))


def chamfered_panel(L, W, T, ch, R, t):
    """Panel L x W x T with a ch x ch chamfer round the top face, moved by R, t."""
    bottom = [(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0)]
    mid = [(0, 0, T - ch), (L, 0, T - ch), (L, W, T - ch), (0, W, T - ch)]
    top = [(ch, ch, T), (L - ch, ch, T), (L - ch, W - ch, T), (ch, W - ch, T)]
    verts = [_apply(R, p, t) for p in bottom + mid + top]
    faces = [
        _Face(_apply(R, (0, 0, -1)), _apply(R, (1, 1, 0), t), L * W),                    # bottom (large)
        _Face(_apply(R, (0, 0, 1)), _apply(R, (L / 2, W / 2, T), t), (L - 2 * ch) * (W - 2 * ch)),
        _Face(_apply(R, (1, 0, 0)), _apply(R, (L, 1, 1), t), W * (T - ch)),
        _Face(_apply(R, (-1, 0, 0)), _apply(R, (0, 1, 1), t), W * (T - ch)),
        _Face(_apply(R, (0, 1, 0)), _apply(R, (1, W, 1), t), L * (T - ch)),
        _Face(_apply(R, (0, -1, 0)), _apply(R, (1, 0, 1), t), L * (T - ch)),
    ]
    return _Body(faces, verts)


class LayFlatTests(unittest.TestCase):
    def setUp(self):
        axis = (1 / math.sqrt(3),) * 3
        self.R = _rot(axis, 37.0)
        self.body = chamfered_panel(60.0, 25.0, 1.8, 0.3, self.R, (12.0, -40.0, 7.0))

    def test_thickness_and_orientation(self):
        up, thickness, zmin = sn_parts.panel_axis(self.body)
        self.assertAlmostEqual(thickness, 1.8, places=9)
        # The large (un-chamfered) face goes down, so "up" is the panel's +Z.
        expected = _apply(self.R, (0, 0, 1))
        self.assertAlmostEqual(sum(a * b for a, b in zip(up, expected)), 1.0, places=9)

    def test_largest_face_up_option(self):
        up, _, _ = sn_parts.panel_axis(self.body, largest_down=False)
        expected = _apply(self.R, (0, 0, -1))
        self.assertAlmostEqual(sum(a * b for a, b in zip(up, expected)), 1.0, places=9)

    def test_placement_matrix_puts_part_on_sheet(self):
        info = sn_parts.analyze(self.body)
        self.assertAlmostEqual(info.size[0], 60.0, places=6)
        self.assertAlmostEqual(info.size[1], 25.0, places=6)
        parts = [info.nest_part(i) for i in range(6)]
        res = nest(parts, NestOptions(sheet_length=122, sheet_width=244, margin=1, spacing=0.6,
                                      method='best', time_budget=0.5))
        self.assertEqual(len(res.sheets), 1)
        for pl in res.sheets[0].placements:
            m = info.matrix(pl.rotation, pl.x, pl.y)
            pts = [m.apply((v.geometry.x, v.geometry.y, v.geometry.z)) for v in self.body.vertices]
            xs, ys, zs = zip(*pts)
            self.assertAlmostEqual(min(zs), 0.0, places=9)
            self.assertAlmostEqual(max(zs), 1.8, places=9)
            self.assertAlmostEqual(min(xs), pl.bbox[0], places=6)
            self.assertAlmostEqual(max(xs), pl.bbox[2], places=6)
            self.assertAlmostEqual(min(ys), pl.bbox[1], places=6)
            self.assertAlmostEqual(max(ys), pl.bbox[3], places=6)
            # Bottom vertices (first four) span the full 60 x 25 footprint.
            bx = [p[0] for p in pts[:4]]
            by = [p[1] for p in pts[:4]]
            self.assertAlmostEqual(max(bx) - min(bx) + max(by) - min(by), 85.0, places=6)
            self.assertTrue(all(abs(p[2]) < 1e-9 for p in pts[:4]))


if __name__ == '__main__':
    unittest.main()
