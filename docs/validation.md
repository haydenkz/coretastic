# Validation status

## Automated checks

The selector and both integrated applications have been cross-compiled locally. All three generated partition tables were compared byte-for-byte with the canonical partition map. Application/bootloader image headers, segment checksums, appended SHA-256 hashes, sizes, and partition bounds were checked against the packaged manifest.

Host tests exercise button debouncing, countdown rollover, held-button behavior, full flash-range write guards, NVS routing, startup handoff permissions, OTA rejection, and distinct MAC derivation. They also rasterize the selector screens, checking framebuffer addressing, glyph folding, the selection highlight, and that the generated brand tiles stay in step with the shipped artwork. Python and browser tests reject corrupted images, changed layouts, out-of-bounds updates, and backups with the wrong device identity or checksum. The browser write-operation tests assert that failed preflight checks issue no writes and that component updates do not erase all flash.

The browser release test validates every actual packaged binary and preflights all write operations through a simulated device. TypeScript checks and Vite production builds run against the pinned esptool-js API. These checks do not emulate an ESP32, Bluetooth controller, OLED, radio, or USB serial device.

## Hardware and browser status

| Environment | Verified result |
| --- | --- |
| Linux development host | Native logic tests, firmware cross-compilation, image validation, browser unit tests and static build |
| Desktop Chrome 152.0.7977.75 on Linux | Flasher DOM loads the release manifest and detects Web Serial; physical USB flashing not tested |
| Windows/macOS USB flashing | Not yet tested |
| Android browser USB flashing | No verified configuration; browsers without Web Serial are rejected |
| Normal MeshCore / Meshtastic Android applications | Protocol code retained; pairing and interoperability not yet device-tested |
| Heltec V4.2/V4.3 OLED and radio | Schematics and upstream pin definitions reviewed; physical operation not yet tested |

Web Serial must be provided by the browser in a secure context. The UI checks this before allowing connection. No WebUSB-to-serial adapter/polyfill is included. [Espressif esptool-js](https://github.com/espressif/esptool-js) documents the underlying browser transport.

Before treating a hardware build as validated, test both board revisions: install over USB; boot and switch repeatedly; pair each Android app; create independent identities/settings/contacts; factory-reset and format each app; verify the other app and selector remain byte-identical outside boot metadata; interrupt an update and recover; restore a private backup; and test reset/watchdog/deep-sleep entry. Record the actual board, host OS, browser/app versions, bundle hashes, and results when those tests are performed.
