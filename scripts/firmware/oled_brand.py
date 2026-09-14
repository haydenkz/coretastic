#!/usr/bin/env python3
"""Generate include/oled_brand.h from the Coretastic, MeshCore, and Meshtastic artwork.

The selector owns a 128x64 one-bit SSD1306. The web flasher already ships the brand
artwork, so this script re-derives monochrome tiles from those same PNGs instead of
adding a second copy. Each firmware row carries its own mark; MeshCore's is its
app-icon glyph and Meshtastic's is the badge with the bolt knocked out. Output is
deterministic; tests/test_oled_brand.py fails when the committed header drifts from
the sources.

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

# Ink thresholds per artwork. White-on-transparent wordmarks use alpha coverage; the
# Meshtastic badge is lit with its dark bolt knocked out of the green field.
MESHCORE_ICON_LEVEL = 0.30
MESHTASTIC_GLYPH_LEVEL = 0.10
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


def meshcore_tile(width, height):
    """The MeshCore app-icon glyph: a stylized antenna mark on a black field.

    The icon is white ink on black, so ink is the intersection of opacity and
    luminance; cropping to that box drops the field before the fit. The glyph is
    mostly hairline at this size, so threshold well below even coverage.
    """
    image = read_png(ASSETS / "meshcore-icon.png")

    def ink(pixel):
        return pixel[3] > 127 and luminance(pixel) > 0.6

    image = crop(image, ink_bounds(image, ink))
    return fit(image, width, height, ink, MESHCORE_ICON_LEVEL)


def meshtastic_tile(width, height):
    """Meshtastic ships no wordmark; its badge is lit with the bolt knocked out."""
    image = read_png(ASSETS / "meshtastic.png")
    image = crop(image, ink_bounds(image, lambda p: p[3] > 127))
    lit_cells = coverage(image, width, height, lambda p: p[3] > 127)
    # The bolt inside the badge is a hairline at this size, so threshold it well below
    # the badge's own coverage to keep the knockout instead of filling the tile solid.
    knocked = threshold(coverage(image, width, height, lambda p: luminance(p) < 0.40), 0.10)
    return [
        [lit_cells[y][x] >= 0.5 and not knocked[y][x] for x in range(width)] for y in range(height)
    ]


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
