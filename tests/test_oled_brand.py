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
        # The badge is lit, so the bolt reads as an interior hole rather than extra ink.
        holes = sum(1 for row in mask[3:-3] for value in row[3:-3] if not value)
        self.assertGreater(holes, 8)

    def test_meshcore_mark_knocks_its_glyph_out_of_the_badge(self):
        tiles = {name: mask for name, width, height, mask in oled_brand.build()}
        meshcore = tiles["kMeshcoreMark"]
        meshtastic = tiles["kMeshtasticMark"]
        # The antenna glyph is knocked out of a lit badge, so a regression that fills the
        # badge solid or drops the glyph to a bare squiggle has to fail here.
        holes = sum(1 for row in meshcore[3:-3] for value in row[3:-3] if not value)
        self.assertGreater(holes, 8)
        # Both marks share one badge silhouette, so the rows clear of the glyph match.
        for row in (*range(5), *range(13, 18)):
            self.assertEqual(meshcore[row], meshtastic[row], f"row {row}")
        # The badge is the lit field the glyph reads against: its edges are lit and its
        # corners are cut, so a bare glyph with no badge behind it fails here.
        width = len(meshcore[0])
        height = len(meshcore)
        for row, column in (
            (0, 0),
            (0, width - 1),
            (height - 1, 0),
            (height - 1, width - 1),
        ):
            self.assertFalse(meshcore[row][column])
        self.assertTrue(meshcore[0][width // 2] and meshcore[-1][width // 2])
        self.assertTrue(meshcore[height // 2][0] and meshcore[height // 2][-1])

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
