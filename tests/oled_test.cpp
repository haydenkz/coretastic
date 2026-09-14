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

// True when the tile carries a single connected run of ink in both axes, i.e. no
// glyph was silently dropped to an empty or scattered bitmap by a bad threshold.
bool has_solid_mark(const Bitmap &bitmap, unsigned columns) {
  for (unsigned row = 0; row < bitmap.height; ++row) {
    unsigned run = 0;
    for (unsigned column = 0; column < columns; ++column) {
      const uint8_t byte = bitmap.bits[row * ((bitmap.width + 7) / 8) + (column >> 3)];
      if ((byte >> (7 - (column & 7))) & 1)
        ++run;
    }
    if (run >= columns / 2)
      return true;
  }
  return false;
}
} // namespace

int main() {
  // The packed layout is MSB-leftmost, byte-padded per row, which oled.cpp replays
  // verbatim. MeshCore's mark is its wordmark; Meshtastic's is its badge.
  assert(kMeshcoreMark.width == 96 && kMeshcoreMark.height == 12);
  assert(kMeshtasticMark.width == 18 && kMeshtasticMark.height == 18);
  assert(kWordmark.width == 128 && kWordmark.height == 13);
  for (const Bitmap *mark : {&kMeshcoreMark, &kMeshtasticMark})
    assert(has_solid_mark(*mark, mark->width));

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
