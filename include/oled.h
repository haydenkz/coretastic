#pragma once
#include "esp_err.h"
esp_err_t oled_init();
esp_err_t oled_show(const char *line1, const char *line2, const char *line3, const char *line4);
