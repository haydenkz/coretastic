#pragma once
#include <cstdint>

namespace coretastic {
// Input is sampled every 10 ms. A complete, debounced press changes selection.
class SelectorState {
public:
  explicit SelectorState(uint8_t previous, uint32_t now)
      : selection_(previous == 1 ? 1 : 0), deadline_(now + 5000) {}
  bool update(bool pressed, uint32_t now) {
    if (pressed != raw_) {
      raw_ = pressed;
      changed_ = now;
    }
    if (raw_ != stable_ && uint32_t(now - changed_) >= 30) {
      stable_ = raw_;
      if (stable_) {
        selection_ ^= 1;
        deadline_ = now + 5000;
      }
    }
    return !raw_ && !stable_ && int32_t(now - deadline_) >= 0;
  }
  uint8_t selection() const { return selection_; }
  unsigned seconds(uint32_t now) const {
    const auto left = int32_t(deadline_ - now);
    return left > 0 ? (unsigned(left) + 999) / 1000 : 0;
  }

private:
  uint8_t selection_;
  uint32_t deadline_;
  uint32_t changed_ = 0;
  bool raw_ = false;
  bool stable_ = false;
};
} // namespace coretastic
