#!/usr/bin/env python3
"""Create the template's own PNG icon, no external assets or packages."""
from pathlib import Path
import struct
import zlib


def inside(x, y, points):
    result = False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def main():
    size = 128
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            color = (int(72 - y * .15), int(108 - y * .24), int(245 - y * .14))
            for dy, tone in ((24, (155, 179, 255)), (12, (204, 217, 255)), (0, (255, 255, 255))):
                polygon = [(25, 47 + dy), (64, 27 + dy), (103, 47 + dy), (64, 67 + dy)]
                if inside(x, y, polygon):
                    color = tone
            rows.extend(color)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(rows))) + chunk(b'IEND', b'')
    path = Path(__file__).resolve().parent.parent / 'public' / 'icon.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


if __name__ == '__main__':
    main()
