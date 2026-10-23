"""Generate the toolbar icons (no third-party packages needed).

Run: python tools/make_icons.py
"""

import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(__file__), '..', 'SheetNester', 'resources')

SHEET = (214, 178, 122, 255)
EDGE = (120, 88, 48, 255)
PART = (64, 140, 120, 255)
PART_EDGE = (34, 90, 76, 255)

# Rectangles in a 0..1 unit square: (x0, y0, x1, y1, fill, outline)
SHAPES = [
    (0.06, 0.18, 0.94, 0.82, SHEET, EDGE),
    (0.13, 0.25, 0.47, 0.56, PART, PART_EDGE),
    (0.52, 0.25, 0.87, 0.47, PART, PART_EDGE),
    (0.52, 0.52, 0.70, 0.75, PART, PART_EDGE),
    (0.13, 0.61, 0.47, 0.75, PART, PART_EDGE),
]


def render(size, ss=4):
    n = size * ss
    stroke = max(1.0, n / 32.0)
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]
    for x0, y0, x1, y1, fill, edge in SHAPES:
        X0, Y0, X1, Y1 = x0 * n, y0 * n, x1 * n, y1 * n
        for y in range(int(Y0), int(Y1)):
            for x in range(int(X0), int(X1)):
                inner = X0 + stroke <= x < X1 - stroke and Y0 + stroke <= y < Y1 - stroke
                px[y][x] = fill if inner else edge
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            acc = [0, 0, 0, 0]
            for dy in range(ss):
                for dx in range(ss):
                    r, g, b, a = px[y * ss + dy][x * ss + dx]
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            a = acc[3]
            if a:
                row += bytes([acc[0] // a, acc[1] // a, acc[2] // a, a // (ss * ss)])
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return png(size, size, b''.join(rows))


def png(w, h, raw):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for name, size in (('16x16', 16), ('32x32', 32), ('64x64', 64),
                       ('16x16@2x', 32), ('32x32@2x', 64)):
        with open(os.path.join(OUT, name + '.png'), 'wb') as f:
            f.write(render(size))
    print('icons written to', os.path.abspath(OUT))
