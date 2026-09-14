# Changelog

## v0.1.0 — 2026-09-13

First tagged release: dual-boot Coretastic for the Heltec WiFi LoRa 32 V4.2/V4.3 OLED.

### Added

- ESP-IDF boot selector with remembered choice, PRG debouncing, and a five-second countdown.
- Branded selector screen: the Coretastic wordmark, one row per firmware with its mark, an inverted highlight band on the selection, and a PRG countdown footer. Bitmap tiles are generated from the shipped artwork by `scripts/firmware/oled_brand.py`.
- Integrated MeshCore BLE Companion and Meshtastic with isolated NVS, filesystems, and guarded flash writes.
- Manifest-driven installation, component updates, backup, restore, and USB recovery from both the browser flasher and the Python CLI.
- Browser flasher with logo-led radio cards, firmware chips in install mode, explicit destructive-confirmation, progress, status, and dark theme.
- Release packaging with image validation, source bundles, and tagged GitHub Pages deployment.
- Host tests for selector logic, screen composition, flash contracts, and generated-art drift.

### Changed

- Pin MeshCore Companion to 1.17.0; 1.17.1 is skipped because of the reported Heltec RX amplifier regression.
- Route boot-time NVS statistics to each firmware's isolated partition and keep the Xtensa integration wrappers explicitly aligned outside LTO.
- Confirm a single supported V4.2/V4.3 OLED family in the CLI, browser flasher, and release manifest; backups recorded under the former V4.2/V4.3 names still restore.
- Restore the selector boot target during `app_main` startup with a scoped OTA-metadata write and direct fallback, avoiding unsafe flash access during static initialization.

### Known limitations

- Physical hardware and Android interoperability validation is pending.
- External ROM tools can overwrite all flash; the device is not memory-isolated from other flash regions.