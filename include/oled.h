#pragma once
#include "esp_err.h"
esp_err_t oled_init();
// Branded selector screen: the Coretastic wordmark, one row per firmware with its
// mark, the selected row inverted, and a PRG/countdown footer.
esp_err_t oled_show_selector(unsigned selected, unsigned seconds);
// Handover screen drawn just before the reboot into the selected firmware.
esp_err_t oled_show_boot(unsigned selected);
// Fatal stop: the selector cannot continue and needs a reset or USB recovery.
esp_err_t oled_show_error(const char *title, const char *detail);
