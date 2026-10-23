"""Small 2D geometry helpers (points are (x, y) tuples)."""

import math


def rotate_points(points, angle_deg):
    """Rotate points counter-clockwise about the origin."""
    if angle_deg % 360 == 0:
        return list(points)
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    # Snap quarter turns so rectangles stay exactly axis aligned.
    if abs(c) < 1e-12:
        c = 0.0
    if abs(s) < 1e-12:
        s = 0.0
    return [(c * x - s * y, s * x + c * y) for x, y in points]


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def triangle_area(a, b, c):
    return 0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))


def convex_hull(points):
    """Andrew's monotone chain. Returns the hull counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_area_rect_angle(points):
    """Angle (degrees) to rotate the points by so that their minimum-area
    bounding rectangle becomes axis aligned with its long side along X."""
    hull = convex_hull(points)
    if len(hull) < 3:
        if len(hull) == 2:
            dx = hull[1][0] - hull[0][0]
            dy = hull[1][1] - hull[0][1]
            return -math.degrees(math.atan2(dy, dx))
        return 0.0

    best = None
    n = len(hull)
    for i in range(n):
        x0, y0 = hull[i]
        x1, y1 = hull[(i + 1) % n]
        edge = math.atan2(y1 - y0, x1 - x0)
        c, s = math.cos(-edge), math.sin(-edge)
        xs = [c * x - s * y for x, y in hull]
        ys = [s * x + c * y for x, y in hull]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        area = w * h
        angle = -math.degrees(edge)
        if h > w:
            angle += 90.0
        # Prefer the rotation closest to the current orientation on ties so
        # parts that are already axis aligned are left alone.
        key = (round(area, 9), abs(_wrap180(angle)))
        if best is None or key < best[0]:
            best = (key, angle)
    return _wrap180(best[1])


def _wrap180(angle):
    a = (angle + 180.0) % 360.0 - 180.0
    # Collapse -180 to 180 for stable comparisons.
    return 180.0 if a == -180.0 else a


def convex_band_xrange(poly, y0, y1):
    """X extent of the convex polygon `poly` clipped to the band y0 <= y <= y1.

    Returns (xmin, xmax) or None when the polygon misses the band. Degenerate
    polygons (segments, points) are handled too."""
    xmin = math.inf
    xmax = -math.inf
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        if y0 <= ay <= y1:
            if ax < xmin:
                xmin = ax
            if ax > xmax:
                xmax = ax
        if n == 1:
            break
        bx, by = poly[(i + 1) % n]
        if ay == by:
            continue
        for yb in (y0, y1):
            if (ay - yb) * (by - yb) < 0:
                t = (yb - ay) / (by - ay)
                x = ax + t * (bx - ax)
                if x < xmin:
                    xmin = x
                if x > xmax:
                    xmax = x
    if xmin > xmax:
        return None
    return xmin, xmax
