"""MaxRects bin packing (Jukka Jylanki, "A Thousand Ways to Pack the Bin")."""

_EPS = 1e-9


class MaxRectsBin:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.free = [(0.0, 0.0, width, height)]

    def find(self, w, h, rule):
        """Best (score, x, y) for a w x h rectangle, or None."""
        best = None
        for fx, fy, fw, fh in self.free:
            if w > fw + _EPS or h > fh + _EPS:
                continue
            if rule == 'bl':
                score = (fy + h, fx)
            elif rule == 'bssf':
                dw, dh = fw - w, fh - h
                score = (min(dw, dh), max(dw, dh))
            else:  # 'baf'
                dw, dh = fw - w, fh - h
                score = (fw * fh - w * h, min(dw, dh))
            if best is None or score < best[0]:
                best = (score, fx, fy)
        return best

    def place(self, x, y, w, h):
        new_free = []
        for fr in self.free:
            fx, fy, fw, fh = fr
            if (x >= fx + fw - _EPS or x + w <= fx + _EPS or
                    y >= fy + fh - _EPS or y + h <= fy + _EPS):
                new_free.append(fr)
                continue
            # Split the intersected free rectangle into up to four maximal parts.
            if x > fx + _EPS:
                new_free.append((fx, fy, x - fx, fh))
            if x + w < fx + fw - _EPS:
                new_free.append((x + w, fy, fx + fw - (x + w), fh))
            if y > fy + _EPS:
                new_free.append((fx, fy, fw, y - fy))
            if y + h < fy + fh - _EPS:
                new_free.append((fx, y + h, fw, fy + fh - (y + h)))
        self.free = _prune(new_free)


def _prune(rects):
    rects = sorted(set(rects), key=lambda r: -(r[2] * r[3]))
    kept = []
    for r in rects:
        rx, ry, rw, rh = r
        contained = False
        for k in kept:
            kx, ky, kw, kh = k
            if (rx >= kx - _EPS and ry >= ky - _EPS and
                    rx + rw <= kx + kw + _EPS and ry + rh <= ky + kh + _EPS):
                contained = True
                break
        if not contained:
            kept.append(r)
    return kept
