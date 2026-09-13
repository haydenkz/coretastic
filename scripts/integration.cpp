// Linked into both upstream apps. This protects supported SDK write paths;
// the ESP32 does not provide a security sandbox between native applications.
#include "esp_flash.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "storage_boundary.h"
#include <cstdlib>
#include <cstring>

#ifndef CORETASTIC_MESHCORE
#error Define the firmware owner
#endif
namespace {
constexpr bool meshcore = CORETASTIC_MESHCORE;
constexpr const char *nvs_label = meshcore ? "mc_nvs" : "mt_nvs";
bool boot_handoff = false;
bool writable(uint32_t address, size_t size) {
  return coretastic::storage_write_allowed(meshcore, address, size) ||
         (boot_handoff && coretastic::contains(0xe000, 0x2000, address, size));
}
const char *own_nvs(const char *label) {
  return label && strcmp(label, "nvs") == 0 ? nvs_label : label;
}
bool permitted_nvs(const char *label) { return label && strcmp(label, nvs_label) == 0; }
} // namespace
extern "C" {
esp_err_t __real_nvs_flash_init_partition(const char *);
esp_err_t __real_nvs_flash_erase_partition(const char *);
esp_err_t __real_nvs_flash_deinit_partition(const char *);
esp_err_t __real_nvs_open_from_partition(const char *, const char *, nvs_open_mode_t,
                                         nvs_handle_t *);
esp_err_t __real_esp_flash_write(esp_flash_t *, const void *, uint32_t, uint32_t);
esp_err_t __real_esp_flash_erase_region(esp_flash_t *, uint32_t, uint32_t);
esp_err_t __real_esp_ota_set_boot_partition(const esp_partition_t *);
void __real_app_main();

esp_err_t __wrap_nvs_flash_init_partition(const char *label) {
  label = own_nvs(label);
  return permitted_nvs(label) ? __real_nvs_flash_init_partition(label) : ESP_ERR_INVALID_STATE;
}
esp_err_t __wrap_nvs_flash_init() { return __wrap_nvs_flash_init_partition(nvs_label); }
esp_err_t __wrap_nvs_flash_erase_partition(const char *label) {
  label = own_nvs(label);
  return permitted_nvs(label) ? __real_nvs_flash_erase_partition(label) : ESP_ERR_INVALID_STATE;
}
esp_err_t __wrap_nvs_flash_erase() { return __wrap_nvs_flash_erase_partition(nvs_label); }
esp_err_t __wrap_nvs_flash_deinit_partition(const char *label) {
  label = own_nvs(label);
  return permitted_nvs(label) ? __real_nvs_flash_deinit_partition(label) : ESP_ERR_INVALID_STATE;
}
esp_err_t __wrap_nvs_flash_deinit() { return __wrap_nvs_flash_deinit_partition(nvs_label); }
esp_err_t __wrap_nvs_open_from_partition(const char *label, const char *name, nvs_open_mode_t mode,
                                         nvs_handle_t *handle) {
  label = own_nvs(label);
  return permitted_nvs(label) ? __real_nvs_open_from_partition(label, name, mode, handle)
                              : ESP_ERR_INVALID_STATE;
}
esp_err_t __wrap_nvs_open(const char *name, nvs_open_mode_t mode, nvs_handle_t *handle) {
  return __wrap_nvs_open_from_partition(nvs_label, name, mode, handle);
}
esp_err_t __wrap_esp_flash_write(esp_flash_t *chip, const void *buffer, uint32_t address,
                                 uint32_t size) {
  if ((chip && chip != esp_flash_default_chip) || !writable(address, size))
    return ESP_ERR_INVALID_STATE;
  return __real_esp_flash_write(chip, buffer, address, size);
}
esp_err_t __wrap_esp_flash_erase_region(esp_flash_t *chip, uint32_t address, uint32_t size) {
  if ((chip && chip != esp_flash_default_chip) || !writable(address, size))
    return ESP_ERR_INVALID_STATE;
  return __real_esp_flash_erase_region(chip, address, size);
}
esp_err_t __wrap_esp_flash_erase_chip(esp_flash_t *) { return ESP_ERR_INVALID_STATE; }
esp_err_t __wrap_spi_flash_write(size_t address, const void *data, size_t size) {
  return __wrap_esp_flash_write(nullptr, data, address, size);
}
esp_err_t __wrap_spi_flash_erase_range(size_t address, size_t size) {
  return __wrap_esp_flash_erase_region(nullptr, address, size);
}
esp_err_t __wrap_spi_flash_erase_sector(size_t sector) {
  if (sector >= 0x1000)
    return ESP_ERR_INVALID_ARG;
  return __wrap_esp_flash_erase_region(nullptr, sector * 4096, 4096);
}
esp_err_t __wrap_esp_flash_write_encrypted(esp_flash_t *, uint32_t, const void *, uint32_t) {
  return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t __wrap_spi_flash_write_encrypted(size_t, const void *, size_t) {
  return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t __wrap_esp_ota_begin(const esp_partition_t *, size_t, esp_ota_handle_t *) {
  return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t __wrap_esp_ota_set_boot_partition(const esp_partition_t *) {
  return ESP_ERR_NOT_SUPPORTED;
}
void __wrap_app_main() {
  const esp_partition_t *factory = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_FACTORY, "selector");
  if (!factory)
    abort();
  // Restore RESET-to-selector before Arduino, the radio, or Bluetooth starts.
  boot_handoff = true;
  const esp_err_t result = __real_esp_ota_set_boot_partition(factory);
  boot_handoff = false;
  if (result != ESP_OK)
    abort();
  uint8_t mac[6];
  ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
  mac[0] = (mac[0] | 2) & 0xfe;
  mac[5] ^= meshcore ? 0x40 : 0x80;
  ESP_ERROR_CHECK(esp_base_mac_addr_set(mac));
  __real_app_main();
}
}
