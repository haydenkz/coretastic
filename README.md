<div align="center">

<img src="web/src/assets/CORETASTIC.png" alt="Coretastic" width="360">

**Dual-boot selector and USB flasher for MeshCore and Meshtastic on one Heltec board.**

[![Build and release](https://github.com/haydenkz/coretastic/actions/workflows/build.yml/badge.svg)](https://github.com/haydenkz/coretastic/actions/workflows/build.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Board](https://img.shields.io/badge/board-Heltec%20V4.2%2FV4.3%20OLED-informational)](#supported-hardware)
[![MeshCore](https://img.shields.io/badge/MeshCore-companion--v1.17.0-2f8f5f)](#supported-hardware)
[![Meshtastic](https://img.shields.io/badge/Meshtastic-v2.7.26.54e0d8d-67b168)](#supported-hardware)

[Web flasher](#web-flasher) · [CLI flasher](#command-line-flasher) · [Build & test](#building-and-testing) · [Architecture](docs/architecture.md)

</div>

---

Each firmware occupies a fixed flash slot with separate settings and Bluetooth identity. Switching reboots the device; it never reflashes either application.

> **Status:** the selector and both integrated firmwares build and pass host tests. Physical OLED, radio, switching, and Android pairing are **not yet validated on hardware** — see [Validation status](docs/validation.md).

## Highlights

- **Boot selector** — the Coretastic wordmark, one row per firmware with its mark, an inverted highlight on the choice, and a five-second PRG countdown.
- **Isolated apps** — factory resets, backups, and writes are contained to each app's own flash region.
- **Web flasher + Python CLI** — install, update, backup, restore, and recovery over USB, validated against the release manifest.
- **Release bundles** — validated images, source archives, and checksums, published with a matching flasher to GitHub Pages.

## Supported hardware

| Board | Flash | Target firmware |
| --- | --- | --- |
| Heltec WiFi LoRa 32 **V4.2 / V4.3 OLED** | 16 MiB | MeshCore `companion-v1.17.0`, Meshtastic `v2.7.26.54e0d8d` |

Not supported: V3, V4 R8, and TFT variants. USB cannot identify the PCB family — the flasher checks the ESP32-S3 and physical flash size, and you confirm the board.

Secure Boot and flash encryption must be **disabled**; the flasher rejects enabled devices.

Pinned application revisions are recorded in [`upstream-lock.json`](upstream-lock.json).

## Install and switch

1. Connect USB with a data cable and the LoRa antenna attached. To enter ROM recovery, hold **PRG**, tap **RESET**, release PRG.
2. Open the [web flasher](#web-flasher) and connect the device. Install from a release bundle.
3. First boot defaults to MeshCore; tap PRG to choose Meshtastic.
4. Configure each firmware in its normal Android app (region/frequency, BLE pairing).

Press **RESET** with PRG released to switch: the selector shows the last choice with a five-second countdown. Tap PRG to change it; hold PRG to pause boot.

Keep the flasher and firmware bundle from the **same Coretastic release**. Do not substitute ordinary upstream binaries. See [Boot and storage](docs/architecture.md) for the flash layout and boot sequence.

### Backup, updates, recovery

- **Component update** — write only the selected app and boot metadata, preserving all settings.
- **Full backup** — read the entire 16 MiB flash into a `.ctbackup` file (includes keys and pairings).
- **Restore** — overwrite the full flash after matching checksums, board, MAC, and partition layout.
- **Selector/bootloader recovery** — repair shared boot files while preserving both apps and their settings.

## Web flasher

The browser flasher is published at **https://coretastic.org/** and works from HTTPS or localhost with Web Serial. It verifies the installed partition table and bootloader, writes only validated compatible images, and lets you download the full browser log.

```sh
npm ci --prefix web
npm run dev --prefix web
```

No Android USB flashing configuration is validated yet; use a desktop browser or the CLI.

## Command-line flasher

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

# Backup, install, update, recover, restore
python scripts/device/flash.py backup  --port /dev/ttyACM0 --board heltec-v4-oled --file before.ctbackup
python scripts/device/flash.py install --port /dev/ttyACM0 --board heltec-v4-oled --manifest release/manifest.json --erase
python scripts/device/flash.py meshcore --port /dev/ttyACM0 --board heltec-v4-oled --manifest release/manifest.json
python scripts/device/flash.py recovery --port /dev/ttyACM0 --board heltec-v4-oled --manifest release/manifest.json
python scripts/device/flash.py restore --port /dev/ttyACM0 --board heltec-v4-oled --file before.ctbackup --erase
```

Use the matching COM port on Windows or `/dev/cu.*` on macOS. Logs append to `coretastic-flash.log` (or `--log`); the web interface downloads its own log.

## Building and testing

Prerequisites: Python 3.11, Node 24, Git, a C++17 compiler. PlatformIO installs the pinned ESP-IDF/Arduino SDKs and cross compilers (Linux: Ubuntu 24.04 in CI).

```sh
git clone --recurse-submodules https://github.com/haydenkz/coretastic.git
cd coretastic
python3.11 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix web

# Host + browser contract tests
python scripts/device/test.py
npm test --prefix web

# Build the three firmware images, package, and validate
python scripts/firmware/build.py all
python scripts/release/package.py --version local --output release
python scripts/release/validate_release.py release/manifest.json
python scripts/release/source_bundle.py
npm run test:release --prefix web

# Build the static flasher with the bundle
mkdir -p web/public/releases && cp release/*.bin release/manifest.json release/SHA256SUMS web/public/releases/
npm run build --prefix web
```

`release/` must not already exist when packaging. Builds use disposable `.build/` checkouts with patches applied in filename order; upstream submodules stay unchanged.

### Format and lint

```sh
ruff check scripts tests && ruff format --check scripts tests
clang-format --dry-run --Werror src/*.cpp include/*.h scripts/firmware/integration.cpp tests/*.cpp tests/idf_stubs/*.h
npm run format:check --prefix web && npm run lint --prefix web
```

Pull requests build all three images and the web app, run tests, validate the bundle, and retain artifacts with linker maps and source material. A `v*` tag publishes the bundle and deploys the matching flasher to GitHub Pages (Pages source must be **GitHub Actions**).

## Repository layout

```
scripts/          Python tooling
  device/         flash.py, layout.py, test.py
  firmware/       build.py, prepare.py, integration.cpp, pio_integration.py, oled_brand.py
  release/        package.py, validate_release.py, source_bundle.py, restore_git.py
src/ include/     Native ESP-IDF selector (src is an IDF component)
meshcore/         Integration config, patches, pinned upstream submodule
meshtastic/       Integration config, patches, pinned upstream submodule
web/              Browser flasher (Vite/TypeScript)
tests/            Host + Python tests (selector, screens, flash, layout)
docs/             architecture.md, maintenance.md, validation.md
```

## Troubleshooting

- **No USB port** — use a data cable, close serial monitors, enter ROM recovery (PRG + RESET); the browser needs HTTPS or localhost.
- **Wrong flash size / chip** — stop: offsets are only defined for the 16 MiB board.
- **Pairing fails after reset/restore of one firmware** — forget that firmware's BLE device in Android and pair again.
- **Layout mismatch on update** — use recovery for a compatible table, or back up and do a destructive install.

## License

Coretastic is GPL-3.0 (see [LICENSE](LICENSE)). MeshCore is MIT (Scott Powell / rippleradios.com); Meshtastic is GPL-3.0; both remain in the pinned submodules. ESP-IDF, esptool-js (Espressif), Arduino-ESP32, and other libraries retain their own licenses.