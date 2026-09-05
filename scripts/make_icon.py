#!/usr/bin/env python3
"""Generate the app icon (PNG, ICO, macOS iconset) without any dependencies.

Design: an equalizer (three bars) hinting a mixer, on a rounded dark tile.
Anti-aliasing is done with signed-distance fields, so it renders fast in
pure Python even at 1024px.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONSET = ASSETS / "iconset"

H, S, L = 265, 0.90, 0.60
BG = (18, 16, 31)  # near-black with a hint of the accent hue
ACCENT = None  # set from HSL below


def hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    c = (1 - abs(2 * light - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = light - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


ACCENT = hsl_to_rgb(H, S, L)

_BAR_DIMS = []  # (cx, cy, hw, hh, r)


def _init_geometry(size: int) -> None:
    bar_w = size * 0.12
    gap = size * 0.08
    cx = size / 2
    heights = (size * 0.30, size * 0.48, size * 0.36)
    baseline = size * 0.76
    _BAR_DIMS.clear()
    for i, hgt in enumerate(heights):
        center_x = cx + (i - 1) * (bar_w + gap)
        _BAR_DIMS.append(
            (center_x, baseline - hgt / 2, bar_w / 2, hgt / 2, bar_w / 2)
        )


def sd_rounded_rect(
    x: float, y: float, cx: float, cy: float, hw: float, hh: float, r: float
) -> float:
    qx = abs(x - cx) - (hw - r)
    qy = abs(y - cy) - (hh - r)
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r


def render(size: int) -> list[list[int]]:
    """Return size x size RGBA rows."""
    _init_geometry(size)
    m = size * 0.06
    tile = (size / 2, size / 2, size / 2 - m, size / 2 - m, size * 0.20)

    rows: list[list[int]] = []
    for py in range(size):
        row: list[int] = []
        for px in range(size):
            x = px + 0.5
            y = py + 0.5
            d_tile = sd_rounded_rect(x, y, *tile)
            d_bar = min(sd_rounded_rect(x, y, *dims) for dims in _BAR_DIMS)
            if d_bar <= 0:
                col, d = ACCENT, d_bar
            elif d_tile <= 0:
                col, d = BG, d_tile
            else:
                col, d = BG, min(d_tile, d_bar)
            alpha = round(max(0.0, min(1.0, 0.5 - d)) * 255)
            row.extend((*col, alpha))
        rows.append(row)
    return rows


def _png_bytes(rows: list[list[int]]) -> bytes:
    size = len(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    return png


def write_png(path: Path, rows: list[list[int]]) -> None:
    path.write_bytes(_png_bytes(rows))


def write_ico(path: Path, sizes: list[int]) -> None:
    pngs = [(size, _png_bytes(render(size))) for size in sorted(set(sizes))]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    offset = 6 + 16 * len(pngs)
    for size, data in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(d for _, d in pngs))


# macOS ICNS PNG chunk types, keyed by pixel size.
_ICNS_TYPES = {
    16: b"icp4",
    32: b"icp5",
    64: b"icp6",
    128: b"ic07",
    256: b"ic08",
    512: b"ic09",
    1024: b"ic10",
}


def write_icns(path: Path, sizes: list[int]) -> None:
    chunks = b""
    for size in sorted(set(sizes)):
        data = _png_bytes(render(size))
        chunks += _ICNS_TYPES[size] + struct.pack(">I", len(data) + 8) + data
    total = 8 + len(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", total) + chunks)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    ICONSET.mkdir(exist_ok=True)

    write_png(ASSETS / "icon.png", render(1024))

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    write_ico(ASSETS / "icon.ico", ico_sizes)
    for size in ico_sizes:
        write_png(ASSETS / f"icon-{size}.png", render(size))

    write_icns(ASSETS / "icon.icns", [16, 32, 64, 128, 256, 512, 1024])

    scheme = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in scheme.items():
        write_png(ICONSET / filename, render(size))

    print(f"wrote icon.png, icon.ico, and {len(scheme)} iconset files")


if __name__ == "__main__":
    main()