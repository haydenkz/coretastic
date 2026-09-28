#include "board_profile.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "oled.h"
#include "selector_state.h"

namespace {
constexpr const char *tag = "selector";
uint32_t now_ms() { return uint32_t(esp_timer_get_time() / 1000); }
void error(const char *message) {
  ESP_LOGE(tag, "%s", message);
  oled_show_error("BOOT STOPPED", message);
  for (;;)
    vTaskDelay(pdMS_TO_TICKS(1000));
}
} // namespace
extern "C" void app_main() {
  // Upstream deep sleep can retain pad holds. The selector's software reboot
  // hides that wake cause from the next application, so release them here.
  gpio_deep_sleep_hold_dis();
  for (gpio_num_t pin : coretastic::kRetainedPins) {
    if (rtc_gpio_is_valid_gpio(pin)) {
      rtc_gpio_hold_dis(pin);
      rtc_gpio_deinit(pin);
    }
    gpio_hold_dis(pin);
  }
  for (const coretastic::PinLevel &output : coretastic::kSafeOutputs) {
    gpio_set_direction(output.pin, GPIO_MODE_OUTPUT);
    gpio_set_level(output.pin, output.level);
  }
  const esp_err_t display = oled_init();
  ESP_LOGI(tag, "OLED: %s", esp_err_to_name(display));
  gpio_config_t button{};
  button.pin_bit_mask = 1ULL << coretastic::kButton;
  button.mode = GPIO_MODE_INPUT;
  button.pull_up_en = GPIO_PULLUP_ENABLE;
  // Failures stop on the error screen; ESP_ERROR_CHECK would abort into a reboot loop.
  if (gpio_config(&button) != ESP_OK)
    error("BUTTON ERROR");
  esp_err_t err = nvs_flash_init_partition("selector_nvs");
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    if (nvs_flash_erase_partition("selector_nvs") != ESP_OK)
      error("NVS ERASE ERROR");
    err = nvs_flash_init_partition("selector_nvs");
  }
  if (err != ESP_OK)
    error("NVS ERROR");
  nvs_handle_t nvs;
  if (nvs_open_from_partition("selector_nvs", "boot", NVS_READWRITE, &nvs) != ESP_OK)
    error("NVS OPEN ERROR");
  uint8_t previous = 0;
  nvs_get_u8(nvs, "selected", &previous);
  coretastic::SelectorState state(previous, now_ms());
  uint32_t drawn = now_ms() - 200;
  for (;;) {
    const uint32_t now = now_ms();
    if (state.update(gpio_get_level(coretastic::kButton) == 0, now))
      break;
    if (uint32_t(now - drawn) >= 100) {
      oled_show_selector(state.selection(), state.seconds(now));
      drawn = now;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  const char *label = state.selection() == 0 ? "meshcore" : "meshtastic";
  const esp_partition_t *app =
      esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, label);
  if (!app)
    error("APP NOT FOUND");
  // set_boot_partition verifies the complete image before committing OTA data.
  // Each boot cycle costs otadata sector erases here and in the app's handoff;
  // at the flash's rated endurance that is tens of thousands of reboots.
  if (esp_ota_set_boot_partition(app) != ESP_OK)
    error("INVALID APP IMAGE");
  if (nvs_set_u8(nvs, "selected", state.selection()) != ESP_OK || nvs_commit(nvs) != ESP_OK)
    error("NVS SAVE ERROR");
  nvs_close(nvs);
  ESP_LOGI(tag, "Rebooting into %s", label);
  oled_show_boot(state.selection());
  esp_restart();
}
