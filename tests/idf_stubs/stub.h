#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdlib>
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_ERR_INVALID_STATE = 1, ESP_ERR_NOT_SUPPORTED = 2,
              ESP_ERR_INVALID_ARG = 3, ESP_ERR_NVS_NOT_INITIALIZED = 4;
#define ESP_ERROR_CHECK(x)                                                                         \
  do {                                                                                             \
    if ((x) != ESP_OK)                                                                             \
      abort();                                                                                     \
  } while (0)
struct esp_flash_t {};
struct esp_partition_t {};
extern esp_flash_t *esp_flash_default_chip;
using esp_ota_handle_t = uint32_t;
using nvs_handle_t = uint32_t;
using nvs_open_mode_t = int;
struct nvs_stats_t {
  size_t used_entries;
  size_t free_entries;
  size_t total_entries;
  size_t namespace_count;
};
constexpr int ESP_PARTITION_TYPE_APP = 0, ESP_PARTITION_SUBTYPE_APP_FACTORY = 0;
extern "C" {
const esp_partition_t *esp_partition_find_first(int, int, const char *);
esp_err_t esp_efuse_mac_get_default(uint8_t *);
esp_err_t esp_base_mac_addr_set(uint8_t *);
}
