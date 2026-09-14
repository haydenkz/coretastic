#!/usr/bin/env python3
"""Generate include/oled_brand.h from the Coretastic, MeshCore, and Meshtastic artwork.

The selector owns a 128x64 one-bit SSD1306. The web flasher already ships the brand
artwork, so this script re-derives monochrome tiles from those same PNGs instead of
adding a second copy. Each firmware row carries its own mark as a bare centered glyph:
MeshCore's icon glyph fitted from its artwork, and Meshtastic's bolt carved from its
badge artwork. Output is deterministic; tests/test_oled_brand.py fails when the
committed header drifts from the sources.

    python scripts/firmware/oled_brand.py            # rewrite the header
    python scripts/firmware/oled_brand.py --preview  # also print the tiles as ASCII
"""

import argparse

from pathlib import Path
from PIL import Image as PillowImage

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "web" / "src" / "assets"
HEADER = ROOT / "include" / "oled_brand.h"

# Mark thresholds. MeshCore's glyph is white ink on a full-bleed black square, so ink
# is the intersection of opacity and luminance; Meshtastic's bolt is dark opaque ink
# carved from the badge face.
BADGE_LIT_LEVEL = 0.7
BADGE_KNOCKOUT_LEVEL = 0.10
MESHCORE_GLYPH_WIDTH = 12
MESHCORE_GLYPH_LEVEL = 0.30
WORDMARK_LEVEL = 0.45


class Image:
    """Decoded 8-bit RGBA raster."""

    def __init__(self, width, height, pixels):
        self.width = width
        self.height = height
        self.pixels = pixels


def read_png(path):
    """Decode a PNG into 8-bit RGBA rows."""
    with PillowImage.open(path) as png:
        rgba = png.convert("RGBA")
    width, height = rgba.size
    raw = rgba.tobytes()
    data = [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]
    return Image(width, height, [data[y * width : (y + 1) * width] for y in range(height)])


def luminance(pixel):
    return (0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]) / 255


def ink_bounds(image, ink):
    xs = []
    ys = []
    for y in range(image.height):
        for x in range(image.width):
            if ink(image.pixels[y][x]):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError("artwork has no ink")
    return min(xs), min(ys), max(xs), max(ys)


def crop(image, bounds):
    x0, y0, x1, y1 = bounds
    return Image(x1 - x0 + 1, y1 - y0 + 1, [row[x0 : x1 + 1] for row in image.pixels[y0 : y1 + 1]])


def coverage(image, width, height, ink):
    """Area-average ink coverage per output cell."""
    cells = []
    for ty in range(height):
        y0 = ty * image.height // height
        y1 = max(y0 + 1, (ty + 1) * image.height // height)
        row = []
        for tx in range(width):
            x0 = tx * image.width // width
            x1 = max(x0 + 1, (tx + 1) * image.width // width)
            total = 0.0
            count = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    total += 1.0 if ink(image.pixels[y][x]) else 0.0
                    count += 1
            row.append(total / count)
        cells.append(row)
    return cells


def threshold(cells, level):
    return [[value >= level for value in row] for row in cells]


def fit(image, width, height, ink, level):
    return threshold(coverage(image, width, height, ink), level)


def pack(mask):
    """Row-major bits, most significant bit leftmost, each row padded to a byte."""
    stride = (len(mask[0]) + 7) // 8
    data = bytearray()
    for row in mask:
        for byte_index in range(stride):
            value = 0
            for bit in range(8):
                index = byte_index * 8 + bit
                if index < len(row) and row[index]:
                    value |= 0x80 >> bit
            data.append(value)
    return bytes(data)


def wordmark_tile(width, height, level):
    image = read_png(ASSETS / "CORETASTIC.png")
    image = crop(image, ink_bounds(image, lambda p: p[3] > 127))
    return fit(image, width, height, lambda p: p[3] > 127, level)


def badge_artwork():
    """The Meshtastic rounded square, cropped to its opaque bounds."""
    image = read_png(ASSETS / "meshtastic.png")
    return crop(image, ink_bounds(image, lambda p: p[3] > 127))


def stamp(width, height, glyph):
    """An empty tile with a glyph mask centered on it."""
    top = (height - len(glyph)) // 2
    left = (width - len(glyph[0])) // 2
    tile = [[False] * width for _ in range(height)]
    for y, row in enumerate(glyph):
        for x, value in enumerate(row):
            if value:
                tile[top + y][left + x] = True
    return tile


def meshcore_tile(width, height):
    """MeshCore's own glyph, fitted from its icon and centered on the tile."""
    image = read_png(ASSETS / "meshcore-icon.png")

    def ink(pixel):
        return pixel[3] > 127 and luminance(pixel) > 0.6

    glyph = crop(image, ink_bounds(image, ink))
    glyph_height = max(1, round(MESHCORE_GLYPH_WIDTH * glyph.height / glyph.width))
    return stamp(
        width, height, fit(glyph, MESHCORE_GLYPH_WIDTH, glyph_height, ink, MESHCORE_GLYPH_LEVEL)
    )


def meshtastic_tile(width, height):
    """Meshtastic's bolt, standing alone: the dark ink carved from the badge face at
    badge resolution, cropped and centered."""
    image = badge_artwork()
    face = threshold(coverage(image, width, height, lambda p: p[3] > 127), BADGE_LIT_LEVEL)
    dark = threshold(
        coverage(image, width, height, lambda p: luminance(p) < 0.40), BADGE_KNOCKOUT_LEVEL
    )
    bolt = [[f and d for f, d in zip(face_row, dark_row)] for face_row, dark_row in zip(face, dark)]
    bolt_image = Image(width, height, bolt)
    bolt_image = crop(bolt_image, ink_bounds(bolt_image, lambda value: value))
    return stamp(width, height, bolt_image.pixels)


TILES = (
    ("kWordmark", wordmark_tile),
    ("kMeshcoreMark", meshcore_tile),
    ("kMeshtasticMark", meshtastic_tile),
)


def build():
    """Return the ordered (name, width, height, mask) tiles."""
    # One screen grammar: a full-width Coretastic header, then a mark and a name per
    # firmware row.
    return [
        ("kWordmark", 128, 13, wordmark_tile(128, 13, WORDMARK_LEVEL)),
        ("kMeshcoreMark", 18, 18, meshcore_tile(18, 18)),
        ("kMeshtasticMark", 18, 18, meshtastic_tile(18, 18)),
    ]


def render(tiles):
    preview = []
    for name, width, height, mask in tiles:
        preview.append(f"--- {name} {width}x{height} ---")
        preview.extend("".join("#" if value else "." for value in row) for row in mask)
        preview.append("")
    return "\n".join(preview)


def header(tiles):
    lines = [
        "// Generated by scripts/firmware/oled_brand.py from web/src/assets/*.png.",
        "// Do not edit by hand; run the script and commit the result.",
        "",
        "#pragma once",
        "",
        '#include "oled_frame.h"',
        "",
        "namespace coretastic {",
        "",
        "// Namespace-scope constexpr keeps these internal to each translation unit,",
        "// which is all the C++11 firmware toolchain allows for non-inline data.",
        "",
    ]
    for name, width, height, mask in tiles:
        data = pack(mask)
        lines.append("// clang-format off")
        lines.append(f"constexpr uint8_t {name}Bits[] = {{")
        for start in range(0, len(data), 12):
            chunk = ", ".join(f"0x{value:02x}" for value in data[start : start + 12])
            lines.append(f"    {chunk},")
        lines.append("};")
        lines.append("// clang-format on")
        lines.append(f"constexpr Bitmap {name}{{{width}, {height}, {name}Bits}};")
        lines.append("")
    lines.append("} // namespace coretastic")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="print the tiles as ASCII")
    args = parser.parse_args()
    tiles = build()
    if args.preview:
        print(render(tiles))
    HEADER.write_text(header(tiles))
    print(f"wrote {HEADER.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
