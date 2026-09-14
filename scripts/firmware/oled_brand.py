#!/usr/bin/env python3
"""Generate include/oled_brand.h from the Coretastic, MeshCore, and Meshtastic artwork.

The selector owns a 128x64 one-bit SSD1306. The web flasher already ships the brand
artwork, so this script re-derives monochrome tiles from those same PNGs instead of
adding a second copy. Each firmware row carries its own mark, and both marks use one
grammar: a lit rounded badge with the mark's glyph knocked out of it. Meshtastic's
artwork ships that badge and MeshCore's antenna glyph is knocked out of the same
outline. Output is deterministic; tests/test_oled_brand.py fails when the committed
header drifts from the sources.

    python scripts/firmware/oled_brand.py            # rewrite the header
    python scripts/firmware/oled_brand.py --preview  # also print the tiles as ASCII
"""

import argparse
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "web" / "src" / "assets"
HEADER = ROOT / "include" / "oled_brand.h"

# Ink thresholds per artwork. White-on-transparent wordmarks use alpha coverage. Both
# firmware marks sit on Meshtastic's badge, so the badge's lit face and the knockout
# carried by it have their own thresholds; neither mark carries a shape of its own.
BADGE_LIT_LEVEL = 0.5
BADGE_KNOCKOUT_LEVEL = 0.10
MESHCORE_GLYPH_LEVEL = 0.30
MESHCORE_GLYPH_WIDTH = 12  # fitted inside the 18px badge, where the Meshtastic bolt sits
WORDMARK_LEVEL = 0.45


class Image:
    """Decoded 8-bit RGBA raster."""

    def __init__(self, width, height, pixels):
        self.width = width
        self.height = height
        self.pixels = pixels


def read_png(path):
    """Decode an 8-bit non-interlaced PNG into RGBA rows."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    offset = 8
    idat = []
    width = height = 0
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            width, height, bit_depth, color, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload[:13]
            )
            if bit_depth != 8 or interlace != 0:
                raise ValueError(f"{path}: only 8-bit non-interlaced PNGs are supported")
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            break
        offset += 12 + length
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    stride = width * channels
    raw = zlib.decompress(b"".join(idat))
    scan = bytearray(height * stride)
    position = 0
    for y in range(height):
        filter_type = raw[position]
        position += 1
        row = raw[position : position + stride]
        position += stride
        base = y * stride
        previous = base - stride
        for x in range(stride):
            left = scan[base + x - channels] if x >= channels else 0
            up = scan[previous + x] if y else 0
            upper_left = scan[previous + x - channels] if (y and x >= channels) else 0
            value = row[x]
            if filter_type == 0:
                result = value
            elif filter_type == 1:
                result = value + left
            elif filter_type == 2:
                result = value + up
            elif filter_type == 3:
                result = value + ((left + up) >> 1)
            else:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
                result = value + predictor
            scan[base + x] = result & 0xFF
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            index = y * stride + x * channels
            if channels == 4:
                row.append(tuple(scan[index : index + 4]))
            elif channels == 3:
                row.append((scan[index], scan[index + 1], scan[index + 2], 255))
            elif channels == 2:
                row.append((scan[index],) * 3 + (scan[index + 1],))
            else:
                row.append((scan[index],) * 3 + (255,))
        rows.append(row)
    return Image(width, height, rows)


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
    """The Meshtastic rounded square, cropped to the badge that carries the grammar."""
    image = read_png(ASSETS / "meshtastic.png")
    return crop(image, ink_bounds(image, lambda p: p[3] > 127))


def badge(width, height):
    """The lit rounded square both firmware marks sit on.

    Lit is opaque artwork that is not dark ink. The bolt is dark, so it drops out here
    too; the cells outside the rounded corners are transparent, and transparency reads as
    dark as well, so they drop out with it and leave the rounded outline. Meshtastic's
    alpha carries that outline, so it defines the shape; MeshCore's icon is a full-bleed
    black square with no corner shape of its own and borrows this one, which is what makes
    the two rows read as one grammar instead of a badge beside a floating glyph.
    """
    image = badge_artwork()
    lit = threshold(coverage(image, width, height, lambda p: p[3] > 127), BADGE_LIT_LEVEL)
    knocked = threshold(
        coverage(image, width, height, lambda p: luminance(p) < 0.40), BADGE_KNOCKOUT_LEVEL
    )
    return [
        [face and not hole for face, hole in zip(face_row, hole_row)]
        for face_row, hole_row in zip(lit, knocked)
    ]


def knock_out(width, height, glyph):
    """The shared badge with a glyph cleared out of its center."""
    top = (height - len(glyph)) // 2
    left = (width - len(glyph[0])) // 2
    tile = [list(row) for row in badge(width, height)]
    for y in range(len(glyph)):
        for x in range(len(glyph[0])):
            if glyph[y][x]:
                tile[top + y][left + x] = False
    return tile


def meshcore_tile(width, height):
    """The MeshCore mark in the Meshtastic badge's grammar: its antenna glyph knocked out
    of the same rounded square.

    The icon is white ink on a full-bleed black square, so only the glyph is taken from it
    and its opaque field is dropped. Ink is the intersection of opacity and luminance, and
    cropping to that box removes the field. The glyph is fitted well inside the badge so it
    keeps the Meshtastic bolt's margin instead of touching the outline; it is mostly
    hairline at this size, so it is thresholded below even coverage.
    """
    image = read_png(ASSETS / "meshcore-icon.png")

    def ink(pixel):
        return pixel[3] > 127 and luminance(pixel) > 0.6

    glyph = crop(image, ink_bounds(image, ink))
    glyph_height = max(1, round(MESHCORE_GLYPH_WIDTH * glyph.height / glyph.width))
    return knock_out(
        width, height, fit(glyph, MESHCORE_GLYPH_WIDTH, glyph_height, ink, MESHCORE_GLYPH_LEVEL)
    )


def meshtastic_tile(width, height):
    """Meshtastic ships the badge itself; its own bolt is already the knockout."""
    return badge(width, height)


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
