#include "oled.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "oled_screen.h"
#include <cstring>

namespace {
constexpr i2c_port_t port = I2C_NUM_0;
// The framebuffer and the I2C payload together exceed the main task's stack, and
// the selector is single-threaded, so both live in static storage.
coretastic::Frame frame;
uint8_t payload[1 + coretastic::Frame::kPages * coretastic::Frame::kWidth];

esp_err_t send(const uint8_t *bytes, size_t size) {
  return i2c_master_write_to_device(port, 0x3c, bytes, size, pdMS_TO_TICKS(100));
}
// One control byte followed by the whole framebuffer in the panel's page layout.
esp_err_t show() {
  payload[0] = 0x40;
  std::memcpy(payload + 1, frame.pages(), sizeof(payload) - 1);
  const uint8_t address[] = {0, 0x21, 0, 127, 0x22, 0, 7};
  const esp_err_t err = send(address, sizeof(address));
  return err == ESP_OK ? send(payload, sizeof(payload)) : err;
}
} // namespace

esp_err_t oled_init() {
  // V4.2/V4.3 Vext uses an inverting MOSFET before the OLED supply LDO.
  gpio_set_direction(GPIO_NUM_36, GPIO_MODE_OUTPUT);
  gpio_set_level(GPIO_NUM_36, 0);
  gpio_set_direction(GPIO_NUM_21, GPIO_MODE_OUTPUT);
  gpio_set_level(GPIO_NUM_21, 0);
  vTaskDelay(pdMS_TO_TICKS(20));
  gpio_set_level(GPIO_NUM_21, 1);
  vTaskDelay(pdMS_TO_TICKS(20));
  i2c_config_t config{};
  config.mode = I2C_MODE_MASTER;
  config.sda_io_num = GPIO_NUM_17;
  config.scl_io_num = GPIO_NUM_18;
  config.sda_pullup_en = GPIO_PULLUP_ENABLE;
  config.scl_pullup_en = GPIO_PULLUP_ENABLE;
  config.master.clk_speed = 100000;
  esp_err_t err = i2c_param_config(port, &config);
  if (err != ESP_OK)
    return err;
  err = i2c_driver_install(port, config.mode, 0, 0, 0);
  if (err != ESP_OK)
    return err;
  const uint8_t init[] = {0,    0xae, 0xd5, 0x80, 0xa8, 0x3f, 0xd3, 0,    0x40,
                          0x8d, 0x14, 0x20, 0,    0xa1, 0xc8, 0xda, 0x12, 0x81,
                          0x7f, 0xd9, 0xf1, 0xdb, 0x40, 0xa4, 0xa6, 0xaf};
  return send(init, sizeof(init));
}

esp_err_t oled_show_selector(unsigned selected, unsigned seconds) {
  coretastic::compose_selector(frame, selected, seconds);
  return show();
}

esp_err_t oled_show_boot(unsigned selected) {
  coretastic::compose_boot(frame, selected);
  return show();
}

esp_err_t oled_show_error(const char *title, const char *detail) {
  coretastic::compose_error(frame, title, detail);
  return show();
}
