#include "../scripts/integration.cpp"
#include "stub.h"
#include <cassert>
#include <cstring>
#include <string>

esp_flash_t chip;
esp_flash_t *esp_flash_default_chip = &chip;
static esp_partition_t factory;
static std::string last_nvs;
static unsigned writes = 0, erases = 0;
static bool application_started = false;
extern "C" {
esp_err_t __real_nvs_flash_init_partition(const char *p) {
  last_nvs = p;
  return ESP_OK;
}
esp_err_t __real_nvs_flash_erase_partition(const char *p) {
  last_nvs = p;
  return ESP_OK;
}
esp_err_t __real_nvs_flash_deinit_partition(const char *p) {
  last_nvs = p;
  return ESP_OK;
}
esp_err_t __real_nvs_open_from_partition(const char *p, const char *, nvs_open_mode_t,
                                         nvs_handle_t *) {
  last_nvs = p;
  return ESP_OK;
}
esp_err_t __real_esp_flash_write(esp_flash_t *, const void *, uint32_t, uint32_t) {
  ++writes;
  return ESP_OK;
}
esp_err_t __real_esp_flash_erase_region(esp_flash_t *, uint32_t, uint32_t) {
  ++erases;
  return ESP_OK;
}
const esp_partition_t *esp_partition_find_first(int, int, const char *label) {
  assert(strcmp(label, "selector") == 0);
  return &factory;
}
esp_err_t __real_esp_ota_set_boot_partition(const esp_partition_t *p) {
  assert(p == &factory);
  assert(__wrap_esp_flash_erase_region(nullptr, 0xe000, 8192) == ESP_OK);
  // Even the startup handoff cannot erase a neighbouring partition.
  assert(__wrap_esp_flash_erase_region(nullptr, 0xd000, 8192) != ESP_OK);
  return ESP_OK;
}
esp_err_t esp_efuse_mac_get_default(uint8_t *mac) {
  memset(mac, 0x10, 6);
  return ESP_OK;
}
esp_err_t esp_base_mac_addr_set(uint8_t *mac) {
  assert(mac[0] == 0x12 && mac[5] == (CORETASTIC_MESHCORE ? 0x50 : 0x90));
  return ESP_OK;
}
void __real_app_main() {
  application_started = true;
  assert(__wrap_esp_flash_erase_region(nullptr, 0xe000, 8192) != ESP_OK);
}
}
int main() {
  const char *owner = CORETASTIC_MESHCORE ? "mc_nvs" : "mt_nvs";
  assert(__wrap_nvs_flash_init() == ESP_OK && last_nvs == owner);
  assert(__wrap_nvs_flash_erase() == ESP_OK && last_nvs == owner);
  assert(__wrap_nvs_open("ble", 0, nullptr) == ESP_OK && last_nvs == owner);
  assert(__wrap_nvs_open_from_partition("nvs", "ble", 0, nullptr) == ESP_OK && last_nvs == owner);
  assert(__wrap_nvs_open_from_partition("selector_nvs", "boot", 0, nullptr) != ESP_OK);
  assert(__wrap_nvs_flash_erase_partition(CORETASTIC_MESHCORE ? "mt_nvs" : "mc_nvs") != ESP_OK);
  for (uint32_t address = 0; address < 0x1000000; address += 4096) {
    const bool allowed = coretastic::storage_write_allowed(CORETASTIC_MESHCORE, address, 4096);
    assert((__wrap_spi_flash_write(address, nullptr, 4096) == ESP_OK) == allowed);
    assert((__wrap_spi_flash_erase_sector(address / 4096) == ESP_OK) == allowed);
  }
  const auto previous_writes = writes, previous_erases = erases;
  assert(__wrap_esp_flash_erase_chip(nullptr) != ESP_OK);
  assert(__wrap_esp_flash_write_encrypted(nullptr, 0x10000, nullptr, 4096) != ESP_OK);
  assert(__wrap_esp_ota_begin(nullptr, 100, nullptr) != ESP_OK);
  assert(__wrap_esp_ota_set_boot_partition(&factory) != ESP_OK);
  assert(writes == previous_writes && erases == previous_erases);
  __wrap_app_main();
  assert(application_started && erases == previous_erases + 1);
}
