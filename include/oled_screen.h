#pragma once
#include "oled_brand.h"
#include "oled_frame.h"
#include <cstdio>

namespace coretastic {
// The branded screens. Composition is pure so the host tests can rasterize them
// without an SSD1306 attached; oled.cpp only ships the finished framebuffer.
//
// Grammar: the Coretastic wordmark spans the header, every firmware row leads with
// its own mark, and a name follows only where the mark does not already carry one
// (the MeshCore wordmark does; the Meshtastic badge does not). The selection is an
// inverted full-width band and the countdown footer is the PRG affordance.

constexpr unsigned kRowTop = 16;    // first firmware row
constexpr unsigned kRowPitch = 20;  // distance between row bands
constexpr unsigned kRowBand = 18;   // highlight band height
constexpr unsigned kFooterTop = 56; // PRG/countdown line

struct Row {
  const Bitmap &mark;
  const char *label; // Null when the mark is itself the firmware name.
};

// The firmware rows in selection order. A function rather than a namespace-scope
// table because the C++11 firmware toolchain has no inline variables.
inline Row row(unsigned index) {
  return index == 0 ? Row{kMeshcoreMark, nullptr} : Row{kMeshtasticMark, "MESHTASTIC"};
}

inline void draw_row(Frame &frame, const Row &row, unsigned top) {
  frame.bitmap(4, top + (kRowBand - row.mark.height) / 2, row.mark);
  if (row.label)
    frame.text(4 + row.mark.width + 6, top + (kRowBand - 7) / 2, row.label);
}

inline void compose_selector(Frame &frame, unsigned selected, unsigned seconds) {
  frame.clear();
  frame.bitmap(0, 0, kWordmark);
  for (unsigned row_index = 0; row_index < 2; ++row_index) {
    const unsigned top = kRowTop + row_index * kRowPitch;
    draw_row(frame, row(row_index), top);
    if (row_index == selected)
      frame.invert(0, top, Frame::kWidth, kRowBand);
  }
  char countdown[28];
  std::snprintf(countdown, sizeof(countdown), "PRG CHANGE   BOOT %u", seconds);
  frame.text(4, kFooterTop, countdown);
}

// Shown for the moment between committing the boot target and restarting.
inline void compose_boot(Frame &frame, unsigned selected) {
  frame.clear();
  frame.bitmap(0, 0, kWordmark);
  draw_row(frame, row(selected), 28);
  frame.text(4, 48, "BOOTING");
}

// Fatal stop. The wordmark stays so the brand still reads on a failed boot.
inline void compose_error(Frame &frame, const char *title, const char *detail) {
  frame.clear();
  frame.bitmap(0, 0, kWordmark);
  frame.text(4, 22, title);
  frame.text(4, 34, detail);
  frame.text(4, 46, "RESET TO RETRY");
  frame.text(4, 56, "USB RECOVERY PRG RST");
}
} // namespace coretastic