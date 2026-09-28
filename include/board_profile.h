#pragma once
#include "driver/gpio.h"

namespace coretastic {
struct PinLevel {
  gpio_num_t pin;
  int level;
};

// Board wiring for the selector. CMake defines CORETASTIC_BOARD_<ID> from the
// boards/<id>/ directory being built. Only drive pins the board schematic
// assigns; a wrong output can power an RF amplifier.
#if defined(CORETASTIC_BOARD_HELTEC_V4_OLED)
// V4.2/V4.3 Vext uses an inverting MOSFET before the OLED supply LDO.
constexpr gpio_num_t kOledSda = GPIO_NUM_17, kOledScl = GPIO_NUM_18, kOledReset = GPIO_NUM_21;
constexpr gpio_num_t kVext = GPIO_NUM_36;
constexpr int kVextOn = 0;
constexpr gpio_num_t kButton = GPIO_NUM_0;
constexpr PinLevel kSafeOutputs[] = {
    {GPIO_NUM_7, 0}, // RF PA off while selecting.
    {GPIO_NUM_8, 1}, // Radio chip select inactive.
};
// Upstream deep sleep can hold these pads.
constexpr gpio_num_t kRetainedPins[] = {GPIO_NUM_0,  GPIO_NUM_7,  GPIO_NUM_8,
                                        GPIO_NUM_14, GPIO_NUM_21, GPIO_NUM_36};
#elif defined(CORETASTIC_BOARD_HELTEC_V3)
// V3 Vext is active low and also powers the LoRa antenna boost. There is no
// separate PA enable, so GPIO7 stays untouched.
constexpr gpio_num_t kOledSda = GPIO_NUM_17, kOledScl = GPIO_NUM_18, kOledReset = GPIO_NUM_21;
constexpr gpio_num_t kVext = GPIO_NUM_36;
constexpr int kVextOn = 0;
constexpr gpio_num_t kButton = GPIO_NUM_0;
constexpr PinLevel kSafeOutputs[] = {
    {GPIO_NUM_8, 1}, // Radio chip select inactive.
};
constexpr gpio_num_t kRetainedPins[] = {GPIO_NUM_0, GPIO_NUM_8, GPIO_NUM_14, GPIO_NUM_21,
                                        GPIO_NUM_36};
#else
#error "Unknown board: build a selector-<board> environment"
#endif
} // namespace coretastic
