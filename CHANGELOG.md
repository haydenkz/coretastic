# Changelog

## Unreleased

- Pin MeshCore Companion to 1.17.0; skip 1.17.1 because of the reported Heltec RX amplifier regression.

- Route boot-time NVS statistics to each firmware's isolated partition and keep
  the Xtensa integration wrappers explicitly aligned outside LTO.

- Add the ESP-IDF OLED selector for Heltec V4.2/V4.3 with remembered selection and a five-second countdown.
- Restore the selector boot target during `app_main` startup with a scoped OTA-metadata write and direct fallback, avoiding unsafe flash access during static initialization.
- Confirm a single supported V4.2/V4.3 OLED family in the CLI, browser flasher, and release manifest; backups recorded under the former V4.2/V4.3 names still restore.
- Rework the browser flasher around the supported operations with explicit confirmation, progress, status, and a dark theme.
- Integrate pinned MeshCore BLE Companion and Meshtastic with separate NVS/filesystems and guarded updates.
- Add manifest-driven browser and command-line installation, component updates, backup, restore, and USB recovery.
- Add firmware builds, host/browser tests, release artifacts, source packaging, and tagged Pages deployment.
- Physical hardware and Android interoperability validation is pending.
