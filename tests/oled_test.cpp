#include "oled_brand.h"
#include "oled_frame.h"
#include "oled_screen.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>

using coretastic::Bitmap;
using coretastic::Frame;
using coretastic::kMeshcoreMark;
using coretastic::kMeshtasticMark;
using coretastic::kWordmark;

namespace {
std::string raster(const Frame &frame) {
  std::string out;
  for (unsigned y = 0; y < Frame::kHeight; ++y) {
    for (unsigned x = 0; x < Frame::kWidth; ++x)
      out += frame.at(x, y) ? '#' : '.';
    out += '\n';
  }
  return out;
}

unsigned lit(const Frame &frame) {
  unsigned count = 0;
  for (unsigned y = 0; y < Frame::kHeight; ++y)
    for (unsigned x = 0; x < Frame::kWidth; ++x)
      count += frame.at(x, y) ? 1 : 0;
  return count;
}

// Bit (x, y) of a packed tile, MSB-leftmost with each row byte-padded.
bool tile_lit(const Bitmap &bitmap, unsigned x, unsigned y) {
  const uint8_t byte = bitmap.bits[y * ((bitmap.width + 7) / 8) + (x >> 3)];
  return (byte >> (7 - (x & 7))) & 1;
}

// Unlit cells strictly inside the tile, where the knocked-out glyph sits.
unsigned interior_holes(const Bitmap &bitmap) {
  unsigned holes = 0;
  for (unsigned y = 3; y + 3 < bitmap.height; ++y)
    for (unsigned x = 3; x + 3 < bitmap.width; ++x)
      holes += tile_lit(bitmap, x, y) ? 0 : 1;
  return holes;
}

// Both firmware marks carry the same badge grammar: a lit rounded square with the
// glyph knocked out of its center. A bare glyph, a dropped knockout, or an eroded
// outline all fail one of these.
void assert_badge_grammar(const Bitmap &mark) {
  const unsigned right = mark.width - 1;
  const unsigned bottom = mark.height - 1;
  assert(tile_lit(mark, mark.width / 2, 0) && tile_lit(mark, mark.width / 2, bottom));
  assert(tile_lit(mark, 0, mark.height / 2) && tile_lit(mark, right, mark.height / 2));
  assert(!tile_lit(mark, 0, 0) && !tile_lit(mark, right, 0));
  assert(!tile_lit(mark, 0, bottom) && !tile_lit(mark, right, bottom));
  assert(interior_holes(mark) > 8);
}
} // namespace

int main() {
  // The packed layout is MSB-leftmost, byte-padded per row, which oled.cpp replays
  // verbatim. Each firmware row is an 18x18 mark followed by its name.
  assert(kMeshcoreMark.width == 18 && kMeshcoreMark.height == 18);
  assert(kMeshtasticMark.width == 18 && kMeshtasticMark.height == 18);
  assert(kWordmark.width == 128 && kWordmark.height == 13);
  for (const Bitmap *mark : {&kMeshcoreMark, &kMeshtasticMark})
    assert_badge_grammar(*mark);

  // The two marks are the same badge: every row clear of the knocked-out glyph is lit
  // identically, so the rounded silhouette cannot drift apart between them.
  for (unsigned y : {0u, 1u, 2u, 3u, 4u, 13u, 14u, 15u, 16u, 17u})
    for (unsigned x = 0; x < kMeshcoreMark.width; ++x)
      assert(tile_lit(kMeshcoreMark, x, y) == tile_lit(kMeshtasticMark, x, y));

  Frame frame;
  frame.clear();
  assert(lit(frame) == 0);

  // Framebuffer addressing: the page layout must round-trip every pixel.
  frame.pixel(0, 0);
  frame.pixel(127, 63);
  frame.pixel(64, 7);
  frame.pixel(64, 8);
  frame.pixel(63, 7);
  assert(frame.at(0, 0) && frame.at(127, 63) && frame.at(64, 7) && frame.at(64, 8));
  assert(frame.at(63, 7));
  assert(!frame.at(1, 0) && !frame.at(64, 9) && !frame.at(63, 8));
  // Bit N of a page byte holds row N, which is the panel's native layout.
  assert(frame.pages()[0] == 0x01);             // (0,0) is bit 0 of page 0.
  assert(frame.pages()[64] == 0x80);            // (64,7) is bit 7 of page 0.
  assert(frame.pages()[128 + 64] == 0x01);      // (64,8) is bit 0 of page 1.
  assert(frame.pages()[7 * 128 + 127] == 0x80); // (127,63) is bit 7 of page 7.
  frame.clear();
  assert(lit(frame) == 0);

  // Text folds lowercase and renders the glyph table, not blank cells.
  frame.text(0, 0, "meshcore");
  Frame upper;
  upper.text(0, 0, "MESHCORE");
  assert(std::memcmp(frame.pages(), upper.pages(), Frame::kPages * Frame::kWidth) == 0);
  assert(lit(frame) > 0);
  frame.clear();
  frame.text(0, 40, "X");
  assert(lit(frame) == 13); // X fills four columns top and bottom plus the waist.

  // Selector screen: wordmark, both rows, and the footer are all present.
  coretastic::compose_selector(frame, 0, 5);
  const std::string screen = raster(frame);
  assert(screen.find("#") != std::string::npos);
  assert(lit(frame) > 400);
  for (unsigned y = 0; y < 13; ++y) // the wordmark band
    for (unsigned x = 0; x < 128; ++x)
      if (kWordmark.bits[y * 16 + (x >> 3)] >> (7 - (x & 7)) & 1)
        assert(frame.at(x, y));

  // Selection is shown by inverting exactly one row band, so the two selections
  // differ and each differs from an unselected render.
  Frame first;
  Frame second;
  coretastic::compose_selector(first, 0, 5);
  coretastic::compose_selector(second, 1, 5);
  assert(std::memcmp(first.pages(), second.pages(), Frame::kPages * Frame::kWidth) != 0);

  // The countdown is live: a changed second changes the pixels.
  Frame later;
  coretastic::compose_selector(later, 0, 4);
  assert(std::memcmp(first.pages(), later.pages(), Frame::kPages * Frame::kWidth) != 0);

  // Both rows are named beside their mark, clear of it; the icon alone does not
  // carry the firmware name, so a dropped label would ship an unlabelled row.
  for (unsigned row_index = 0; row_index < 2; ++row_index) {
    const Frame &plain = row_index == 0 ? second : first; // the un-inverted render
    const unsigned x = 4 + kMeshcoreMark.width + 6;
    const unsigned y =
        coretastic::kRowTop + row_index * coretastic::kRowPitch + (coretastic::kRowBand - 7) / 2;
    unsigned inked = 0;
    for (unsigned column = 0; column < 11 * Frame::kAdvance; ++column)
      for (unsigned line = 0; line < 7; ++line)
        inked += plain.at(x + column, y + line) ? 1 : 0;
    assert(inked > 0);
  }

  // The footer is the countdown alone, centered on the panel: no PRG hint at the
  // left margin, and no ink outside the centered run.
  const unsigned footer_width = 6 * Frame::kAdvance; // "BOOT N"
  const unsigned footer_x = (Frame::kWidth - footer_width) / 2;
  unsigned footer_ink = 0;
  for (unsigned x = 0; x < Frame::kWidth; ++x)
    for (unsigned y = coretastic::kFooterTop; y < Frame::kHeight; ++y) {
      footer_ink += first.at(x, y) ? 1 : 0;
      if (x < footer_x || x >= footer_x + footer_width)
        assert(!first.at(x, y));
    }
  assert(footer_ink > 0);

  // Selection inverts the whole band, not just the mark: the empty right margin of
  // the selected row is lit and the same margin of the unselected row is dark.
  for (unsigned x = 96; x < Frame::kWidth; ++x) {
    assert(first.at(x, coretastic::kRowTop + 1));
    assert(!second.at(x, coretastic::kRowTop + 1));
  }

  // The two highlight bands are separated and the footer band is untouched.
  for (unsigned x = 0; x < 128; ++x)
    for (unsigned y = 13; y < 16; ++y)
      assert(!first.at(x, y));
  for (unsigned x = 0; x < 128; ++x)
    for (unsigned y = 34; y < 36; ++y)
      assert(!first.at(x, y));
  for (unsigned x = 0; x < 128; ++x)
    for (unsigned y = 54; y < 56; ++y)
      assert(!first.at(x, y));

  coretastic::compose_boot(frame, 1);
  assert(lit(frame) > 200);
  coretastic::compose_error(frame, "NVS ERROR", "detail");
  assert(lit(frame) > 200);

  std::puts("oled tests passed");
}
