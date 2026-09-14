#pragma once
#include "oled_brand.h"
#include "oled_frame.h"
#include <cstdio>

namespace coretastic {
// The branded selector screen. Composition is pure so the host tests can rasterize
// it without an SSD1306 attached; oled.cpp only ships the finished framebuffer.
inline void compose_selector(Frame &frame, unsigned selected, unsigned seconds) {
  frame.clear();
  frame.bitmap(0, 0, kWordmark);
  const Bitmap marks[] = {kMeshcoreMark, kMeshtasticMark};
  const char *labels[] = {"MESHCORE", "MESHTASTIC"};
  for (unsigned row = 0; row < 2; ++row) {
    // Marks are 18 px tall on a 20 px pitch, leaving a clear separator row between
    // the two highlight bands and keeping the footer band untouched.
    const unsigned top = 15 + row * 20;
    frame.bitmap(4, top, marks[row]);
    frame.text(4 + marks[row].width + 6, top + (marks[row].height - 7) / 2, labels[row]);
    if (row == selected)
      frame.invert(0, top, Frame::kWidth, marks[row].height);
  }
  char countdown[28];
  std::snprintf(countdown, sizeof(countdown), "PRG CHANGE   BOOT %u", seconds);
  frame.text(4, 55, countdown);
}

// Shown for the moment between committing the boot target and restarting.
inline void compose_boot(Frame &frame, unsigned selected) {
  frame.clear();
  frame.bitmap(0, 0, kWordmark);
  const Bitmap marks[] = {kMeshcoreMark, kMeshtasticMark};
  const char *labels[] = {"MESHCORE", "MESHTASTIC"};
  frame.bitmap(4, 32, marks[selected]);
  frame.text(4 + marks[selected].width + 6, 36, "BOOTING");
  frame.text(4 + marks[selected].width + 6, 46, labels[selected]);
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
