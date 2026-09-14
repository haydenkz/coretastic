#pragma once
#include <cstdint>
#include <cstring>

namespace coretastic {
// A one-bit image. Rows are packed most significant bit leftmost and each row is
// padded to a whole byte, which is how oled_brand.py emits the generated tiles.
struct Bitmap {
  uint8_t width;
  uint8_t height;
  const uint8_t *bits; // Row-major, most significant bit leftmost.
};

// Original 5x7 glyphs, columns, bit zero at the top. Only UI characters needed.
constexpr uint8_t kGlyphs[][5] = {
    {0x7e, 0x11, 0x11, 0x11, 0x7e}, {0x7f, 0x49, 0x49, 0x49, 0x36}, {0x3e, 0x41, 0x41, 0x41, 0x22},
    {0x7f, 0x41, 0x41, 0x22, 0x1c}, {0x7f, 0x49, 0x49, 0x49, 0x41}, {0x7f, 0x09, 0x09, 0x09, 0x01},
    {0x3e, 0x41, 0x49, 0x49, 0x3a}, {0x7f, 0x08, 0x08, 0x08, 0x7f}, {0, 0x41, 0x7f, 0x41, 0},
    {0x20, 0x40, 0x41, 0x3f, 0x01}, {0x7f, 0x08, 0x14, 0x22, 0x41}, {0x7f, 0x40, 0x40, 0x40, 0x40},
    {0x7f, 0x02, 0x0c, 0x02, 0x7f}, {0x7f, 0x04, 0x08, 0x10, 0x7f}, {0x3e, 0x41, 0x41, 0x41, 0x3e},
    {0x7f, 0x09, 0x09, 0x09, 0x06}, {0x3e, 0x41, 0x51, 0x21, 0x5e}, {0x7f, 0x09, 0x19, 0x29, 0x46},
    {0x26, 0x49, 0x49, 0x49, 0x32}, {0x01, 0x01, 0x7f, 0x01, 0x01}, {0x3f, 0x40, 0x40, 0x40, 0x3f},
    {0x1f, 0x20, 0x40, 0x20, 0x1f}, {0x3f, 0x40, 0x30, 0x40, 0x3f}, {0x63, 0x14, 0x08, 0x14, 0x63},
    {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43}, {0x3e, 0x51, 0x49, 0x45, 0x3e},
    {0, 0x42, 0x7f, 0x40, 0},       {0x62, 0x51, 0x49, 0x49, 0x46}, {0x22, 0x41, 0x49, 0x49, 0x36},
    {0x18, 0x14, 0x12, 0x7f, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39}, {0x3e, 0x49, 0x49, 0x49, 0x32},
    {0x01, 0x71, 0x09, 0x05, 0x03}, {0x36, 0x49, 0x49, 0x49, 0x36}, {0x26, 0x49, 0x49, 0x49, 0x3e},
};
// Returns the glyph for an ASCII character, folding lowercase to uppercase.
inline const uint8_t *glyph_for(char value) {
  if (value >= 'a' && value <= 'z')
    value = char(value - 'a' + 'A');
  if (value >= 'A' && value <= 'Z')
    return kGlyphs[value - 'A'];
  if (value >= '0' && value <= '9')
    return kGlyphs[26 + value - '0'];
  return nullptr;
}

// The selector's 128x64 SSD1306 framebuffer in the panel's native layout: one
// byte per column within a page, bit N holding row N of that page. Composition
// is kept free of ESP-IDF so the host tests can rasterize it.
class Frame {
public:
  static constexpr unsigned kWidth = 128;
  static constexpr unsigned kHeight = 64;
  static constexpr unsigned kPages = kHeight / 8;
  static constexpr unsigned kGlyphWidth = 5;
  static constexpr unsigned kAdvance = 6;

  void clear() { std::memset(pages_, 0, sizeof(pages_)); }

  void pixel(unsigned x, unsigned y, bool on = true) {
    if (x >= kWidth || y >= kHeight)
      return;
    const uint8_t mask = uint8_t(1u << (y & 7));
    uint8_t &cell = pages_[(y >> 3) * kWidth + x];
    cell = on ? uint8_t(cell | mask) : uint8_t(cell & ~mask);
  }

  bool at(unsigned x, unsigned y) const {
    if (x >= kWidth || y >= kHeight)
      return false;
    return (pages_[(y >> 3) * kWidth + x] >> (y & 7)) & 1;
  }

  // Uppercase 5x7 text, one blank column between glyphs. Lowercase is folded.
  void text(unsigned x, unsigned y, const char *value) {
    if (!value)
      return;
    for (unsigned index = 0; value[index]; ++index) {
      const uint8_t *glyph = glyph_for(value[index]);
      if (!glyph)
        continue;
      for (unsigned column = 0; column < kGlyphWidth; ++column)
        for (unsigned row = 0; row < 7; ++row)
          if (glyph[column] >> row & 1)
            pixel(x + index * kAdvance + column, y + row);
    }
  }

  void bitmap(unsigned x, unsigned y, const Bitmap &bitmap) {
    const unsigned stride = (bitmap.width + 7) / 8;
    for (unsigned row = 0; row < bitmap.height; ++row)
      for (unsigned column = 0; column < bitmap.width; ++column) {
        const uint8_t byte = bitmap.bits[row * stride + (column >> 3)];
        pixel(x + column, y + row, (byte >> (7 - (column & 7))) & 1);
      }
  }

  void invert(unsigned x, unsigned y, unsigned width, unsigned height) {
    for (unsigned row = 0; row < height; ++row)
      for (unsigned column = 0; column < width; ++column)
        if (x + column < kWidth && y + row < kHeight)
          pixel(x + column, y + row, !at(x + column, y + row));
  }

  const uint8_t *pages() const { return pages_; }

private:
  uint8_t pages_[kPages * kWidth] = {};
};

} // namespace coretastic
