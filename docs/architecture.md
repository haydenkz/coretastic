# Boot and storage integration

## Hardware sources

Reviewed hardware: Heltec OLED V4.2/V4.3, ESP32-S3R2 and W25Q128 (16 MiB). The selector uses OLED SDA GPIO17, SCL GPIO18, reset GPIO21, and address `0x3c`. GPIO36 drives the inverting Vext power circuit: low enables the OLED supply. PRG is active-low GPIO0; RESET drives CHIP_PU. Radio PA control is left to the upstream firmware.

References:

- [Heltec V4.2 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/WiFi_LoRa_32_V4.2.pdf)
- [Heltec V4.3 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf)
- [Heltec R8 pin changes](https://github.com/HelTecAutomation/HeltecWiKi/blob/main/docs/devices/open-source-hardware/esp32-series/lora-32/wifi-lora-32-v4_r8_board/wifi-lora-32-v4_r8_board.md)
- [MeshCore pinned V4 definitions](https://github.com/meshcore-dev/MeshCore/tree/d92964352441e53b93e8667b802e04f6e072b39e/variants/heltec_v4)
- [Meshtastic pinned V4 definitions](https://github.com/meshtastic/firmware/tree/54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb/variants/esp32s3/heltec_v4)

Both applications retain their upstream V4.2/V4.3 RF front-end detection and normal BLE protocols. R8 has different GPIO assignments and is excluded. Hardware MAC and JEDEC flash ID are checked over USB, but PCB revision is a user-confirmed property.

## Flash layout

`partitions.csv` is the authoritative layout. Sector size is 4096 bytes; application offsets are 64 KiB aligned.

| Range start | Size | Owner / purpose |
| --- | --- | --- |
| `0x000000` | `0x008000` | Shared ESP-IDF bootloader region |
| `0x008000` | `0x001000` | Partition table |
| `0x009000` | `0x005000` | Selector NVS |
| `0x00e000` | `0x002000` | Boot-selection metadata |
| `0x010000` | `0x0f0000` | Factory selector |
| `0x100000` | `0x300000` | MeshCore, OTA slot 0 |
| `0x400000` | `0x600000` | Meshtastic, OTA slot 1 |
| `0xa00000` | `0x010000` | MeshCore NVS |
| `0xa10000` | `0x010000` | Meshtastic NVS |
| `0xa20000` | `0x100000` | MeshCore SPIFFS |
| `0xb20000` | `0x4e0000` | Meshtastic LittleFS |

The OTA slot names are used only for ESP-IDF boot selection. They are separate applications, not redundant update slots. The flash contains no upstream OTA loader or shared default `nvs` partition. Both filesystem entries use the ESP-IDF `spiffs` subtype expected by these Arduino libraries; their labels select distinct regions and filesystem formats.

## Boot sequence

An erased OTA data partition boots the factory selector. The selector reads `selector_nvs/boot/selected`, debounces PRG, and displays a five-second countdown. It verifies the chosen image through `esp_ota_set_boot_partition`, commits the choice in its own NVS, then reboots.

A link wrapper around each upstream `app_main` restores the factory boot target before calling Arduino startup. Its temporary boot-metadata write permission is closed before starting the application. Later firmware reset, factory reset, or watchdog restart therefore normally returns to the selector. This also means a deep-sleep wake passes through the selector after an application has initialized; low-power behavior has not been measured.

A crash before the wrapper runs can leave the selected OTA target pending. Corrupt selector/bootloader images, failed pre-startup handoff, and interrupted USB writes require ROM USB recovery. GPIO0 is never configured as a bootloader factory-reset input, and no bootloader GPIO action erases settings.

The bootloader and selector use ESP-IDF 4.4.7. Both applications use Arduino-ESP32 2.0.17's ESP-IDF 4.4.7 SDK. This avoids relying on a newer bootloader accepting older application formats. [Espressif bootloader compatibility guidance](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-guides/bootloader.html) explains the forward-compatibility guarantee.

## Isolation and updates

Shared link wrappers redirect default NVS initialization, open, erase, and deinitialization to `mc_nvs` or `mt_nvs`. Explicit access to the other NVS partition is rejected. This includes SDK Bluetooth bond storage and upstream factory-reset calls. Filesystem mounts are explicitly labelled; formatting follows the same mounted partition.

The wrappers restrict public ESP flash and legacy Arduino SPI flash write/erase entry points to the calling application's NVS/filesystem ranges. Full-chip erase and encrypted writes are rejected. An upstream OTA begin or boot-target change returns an error. Meshtastic's OTA loader lookup returns no partition, so the app's existing OTA capability check declines the request. MeshCore Wi-Fi OTA is disabled at build time. The public boot metadata is writable by an upstream app only during the early selector handoff.

Each app derives a distinct locally administered base MAC from the eFuse address, preserving that address across resets and updates. The eFuse hardware address itself is unchanged. Separate radio identities, keys, contacts, and settings live in the respective application stores.

The USB tools use `manifest.json` for version, image addresses, sizes, hashes, board list, layout, and storage-format epoch. Both validate the authoritative partition contract and image header/segment checksum/embedded SHA-256. Updates compare the installed partition table and bootloader before writing. Images are validated before any erase; written ranges are verified afterward. Recovery can replace the bootloader while preserving settings only if the table matches. Complete installation and backup restoration require an explicit destructive-operation acknowledgement.

A manifest checksum detects corruption; it does not authenticate a release publisher. Obtain the matching flasher and bundle from a trusted release. External ROM tools can overwrite all flash, and native firmware is not memory-isolated from other flash regions.
