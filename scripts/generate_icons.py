#!/usr/bin/env python3

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "server" / "gizmoapp_server" / "static" / "icons"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int) -> None:
    rows = bytearray()

    for y in range(size):
        rows.append(0)
        for x in range(size):
            nx = x / (size - 1)
            ny = y / (size - 1)
            radial = math.hypot(nx - 0.52, ny - 0.46)
            sweep = 0.5 + 0.5 * math.sin((nx * 4.1) + (ny * 3.6))

            red = int(15 + 18 * (1 - ny) + 60 * sweep)
            green = int(28 + 70 * (1 - radial) + 85 * sweep)
            blue = int(42 + 92 * (1 - radial) + 70 * (1 - nx))

            cx = nx - 0.5
            cy = ny - 0.5
            diamond = abs(cx) + abs(cy)
            ring = abs(math.hypot(cx, cy) - 0.18)

            if diamond < 0.23:
                red, green, blue = 241, 207, 135
            elif ring < 0.028:
                red, green, blue = 245, 154, 98
            elif math.hypot(cx + 0.12, cy - 0.08) < 0.085:
                red, green, blue = 114, 209, 194

            rows.extend((red, green, blue, 255))

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png.extend(_chunk(b"IHDR", ihdr))
    png.extend(_chunk(b"IDAT", zlib.compress(bytes(rows), level=9)))
    png.extend(_chunk(b"IEND", b""))
    path.write_bytes(png)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_png(OUTPUT_DIR / "icon-192.png", 192)
    write_png(OUTPUT_DIR / "icon-512.png", 512)
    write_png(OUTPUT_DIR / "apple-touch-icon.png", 180)
    print(f"Wrote icons to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
