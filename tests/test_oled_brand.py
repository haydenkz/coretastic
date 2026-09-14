import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))
import oled_brand


class BrandTests(unittest.TestCase):
    def test_header_matches_generated_output(self):
        # include/oled_brand.h is derived from the shipped artwork; a stale header or a
        # changed source PNG would otherwise ship quietly.
        self.assertEqual(
            oled_brand.HEADER.read_text(),
            oled_brand.header(oled_brand.build()),
            "run scripts/firmware/oled_brand.py and commit the result",
        )

    def test_tiles_are_fully_inked_and_within_the_panel(self):
        for name, width, height, mask in oled_brand.build():
            self.assertTrue(1 <= width <= 128, name)
            self.assertTrue(1 <= height <= 64, name)
            self.assertEqual([len(row) for row in mask], [width] * height, name)
            inked = sum(1 for row in mask for value in row if value)
            # A wrong threshold collapses a mark to nothing or floods the tile solid.
            self.assertGreater(inked, width * height * 0.05, name)
            self.assertLess(inked, width * height * 0.95, name)

    def test_meshtastic_mark_keeps_its_bolt(self):
        tiles = {name: mask for name, width, height, mask in oled_brand.build()}
        mask = tiles["kMeshtasticMark"]
        # The bolt stands alone on an empty tile: real ink in the middle, nothing at
        # the edges a badge silhouette would have occupied.
        inked = sum(1 for row in mask[3:-3] for value in row[3:-3] if value)
        self.assertGreater(inked, 8)
        self.assertFalse(any(mask[0]) or any(mask[-1]))
        self.assertFalse(any(row[0] or row[-1] for row in mask))

    def test_meshcore_mark_is_its_own_glyph(self):
        tiles = {name: mask for name, width, height, mask in oled_brand.build()}
        meshcore = tiles["kMeshcoreMark"]
        meshtastic = tiles["kMeshtasticMark"]
        # Both marks are bare glyphs centered on an empty tile: ink in the middle,
        # nothing at the borders, and neither is a copy of the other.
        for mask in (meshcore, meshtastic):
            inked = sum(1 for row in mask[3:-3] for value in row[3:-3] if value)
            self.assertGreater(inked, 8)
            self.assertFalse(any(mask[0]) or any(mask[-1]))
            self.assertFalse(any(row[0] or row[-1] for row in mask))
        self.assertNotEqual(meshcore, meshtastic)

    def test_packed_bits_match_the_tile(self):
        for name, width, height, mask in oled_brand.build():
            packed = oled_brand.pack(mask)
            stride = (width + 7) // 8
            self.assertEqual(len(packed), stride * height, name)
            for row in range(height):
                for column in range(width):
                    byte = packed[row * stride + (column >> 3)]
                    expected = 1 if (byte >> (7 - (column & 7))) & 1 else 0
                    self.assertEqual(expected, 1 if mask[row][column] else 0, name)


if __name__ == "__main__":
    unittest.main()
