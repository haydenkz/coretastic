# Coretastic

ESP-IDF boot selector and USB flasher for running MeshCore BLE Companion or Meshtastic on one Heltec WiFi LoRa 32 V4 OLED. Each firmware occupies a fixed flash slot and has separate settings and Bluetooth identity. Switching reboots the device; it does not reflash either application.

The integrated firmware builds and host tests run without a device. Physical OLED operation, radio operation, switching, and Android pairing have **not yet been validated on hardware**. See [validation status](docs/validation.md).

## Hardware and prerequisites

- Heltec WiFi LoRa 32 **V4.2 or V4.3 OLED**, ESP32-S3R2, 16 MiB flash and 2 MiB PSRAM.
- V3, V4 R8, TFT variants, and earlier V4 prototypes are unsupported. Read the board marking: USB cannot identify the PCB revision. The flasher checks the ESP32-S3 and physical flash capacity.
- USB data cable and a connected LoRa antenna before operating either radio firmware.
- Secure Boot and flash encryption must be disabled; the utility rejects enabled devices.
- Desktop Chromium browser with Web Serial over HTTPS/localhost, or Python 3.11 and the command-line tool. Close other serial applications before connecting.

Pinned applications: MeshCore `companion-v1.17.1` and Meshtastic `v2.7.26.54e0d8d`. Exact revisions are in [upstream-lock.json](upstream-lock.json).

## Installation

Use a firmware bundle and web flasher from the **same Coretastic release**. Download and extract the bundle from this repository's GitHub Releases when a release is available. Do not substitute ordinary upstream binaries or their factory installers.

1. Connect USB. To enter ROM recovery, hold **PRG**, press and release **RESET**, then release PRG.
2. Open the matching flasher, select the physical board revision, and connect the USB device.
3. Download a full local backup before replacing an existing installation.
4. Select **Complete installation**, type `ERASE`, and run. This erases every setting, identity, key, contact, and pairing currently on the device.
5. After verification, disconnect the flasher and press RESET with PRG released.

The first selector boot defaults to MeshCore. Use PRG to highlight Meshtastic if wanted. Configure each firmware separately in its normal Android application, including its radio region/frequency and Bluetooth pairing. No custom Android application is required by the integration; interoperability still needs device testing.

## Switching

Press RESET with PRG **released**. The selector shows the previous choice with a five-second countdown. Tap PRG to change the choice and restart the countdown. Release PRG and wait to boot it. Holding PRG pauses boot until it is released.

PRG is GPIO0, an ESP32 boot strap. Holding it during reset enters the ROM downloader, not the OLED menu. RESET is a hardware reset button, not a second menu input.

The integrated applications restore the selector as the next boot target before their Arduino startup. If an image fails before that point, or the selector is damaged, use ROM USB recovery. [Boot and storage details](docs/architecture.md) describe the limits.

## Updates, backup, and recovery

**Component update:** choose MeshCore, Meshtastic, or selector. The flasher verifies the installed partition table and shared bootloader, writes only the selected application and boot-selection metadata, and preserves all settings. Only compatible Coretastic bundles are accepted. Upstream OTA and upstream PlatformIO upload targets are disabled.

**Backup:** read the complete 16 MiB flash into a `.ctbackup` file. Backups include private keys, contacts, and Bluetooth credentials. Browser backups are downloaded locally and are not uploaded. The CLI refuses to overwrite an existing backup filename.

**Restore:** select the backup, type `ERASE`, and restore. Checksums, board choice, original hardware MAC, and partition layout must match. Restoration overwrites the entire flash. Backups of ordinary upstream installations can be saved, but this utility only restores its own dual-boot layout; use the original firmware's recovery tooling for other layouts.

**Selector/bootloader recovery:** enter ROM recovery and choose the recovery operation. It repairs the selector and shared boot files, resets boot metadata, and preserves both applications and their settings. The existing partition table must match. For a damaged/incompatible partition table, back up what remains and use complete installation.

A failed or interrupted write is not reported as success. Re-enter ROM recovery and retry. Component writes are not atomic and do not have an OTA rollback slot.

## Command-line use

From a source checkout, create a Python 3.11 virtual environment and install `requirements.txt`. `--board` confirms the marking you checked physically.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/flash.py backup --port /dev/ttyACM0 --board heltec-v4.3-oled --file before.ctbackup
python scripts/flash.py install --port /dev/ttyACM0 --board heltec-v4.3-oled --manifest release/manifest.json --erase
python scripts/flash.py meshcore --port /dev/ttyACM0 --board heltec-v4.3-oled --manifest release/manifest.json
python scripts/flash.py recovery --port /dev/ttyACM0 --board heltec-v4.3-oled --manifest release/manifest.json
python scripts/flash.py restore --port /dev/ttyACM0 --board heltec-v4.3-oled --file before.ctbackup --erase
```

Use the appropriate COM port on Windows or `/dev/cu.*` port on macOS. Logs append to `coretastic-flash.log`, or the path supplied with `--log`. The web interface also downloads logs.

## Building and testing

Use Python 3.11.16, Node 24.19.0, Git, and a native C++17 compiler. PlatformIO installs the pinned ESP-IDF/Arduino SDKs and cross compilers. Linux builds also need the usual build tools; the GitHub workflow uses Ubuntu 24.04.

```sh
git clone --recurse-submodules https://github.com/haydenkz/coretastic.git
cd coretastic
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix web
python scripts/test.py
npm test --prefix web
python scripts/build.py all
python scripts/package.py --version local --output release
python scripts/validate_release.py release/manifest.json
npm run test:release --prefix web
python scripts/source_bundle.py
mkdir -p web/public/releases
cp release/*.bin release/manifest.json release/SHA256SUMS web/public/releases/
npm run build --prefix web
npm run dev --prefix web
```

`release/` must not already exist when packaging. Builds use disposable `.build/meshcore` and `.build/meshtastic` checkouts with patches applied in filename order. Upstream submodules stay unchanged. The build script recreates generated checkouts when integration inputs change. Generated binaries, checkouts, web assets, and build directories are ignored by Git.

Our root project is native ESP-IDF C++ with `src/` as an IDF component. `platformio.ini` pins ESP-IDF **4.4.7** and its toolchain; `idf.py` can also build the root CMake project with that SDK and `IDF_TARGET=esp32s3`. Both upstreams retain PlatformIO/Arduino. The shared bootloader uses the same ESP-IDF generation as their pinned Arduino SDK.

Formatting and lint commands:

```sh
ruff check scripts tests
ruff format --check scripts tests
clang-format --dry-run --Werror src/*.cpp include/*.h scripts/integration.cpp tests/*.cpp tests/idf_stubs/*.h
npm run format:check --prefix web
npm run lint --prefix web
```

Pull requests build all three firmware images and the web application, run tests, validate the bundle, and retain artifacts with linker maps and source material. A `v*` tag publishes the bundle and deploys the matching flasher/assets to GitHub Pages. The repository's Pages source must be set to **GitHub Actions**.

## Troubleshooting and limitations

- **No USB port:** use a data cable, close serial monitors, and enter ROM recovery using PRG + RESET. A browser needs HTTPS or localhost and Web Serial support.
- **Wrong flash size or chip:** stop and check the board. Flash offsets are only defined for the supported 16 MiB board.
- **No OLED:** USB recovery remains available. Check the board revision and use the correct OLED build. Do not guess R8 power pins.
- **Pairing fails after restoring/resetting one firmware:** forget that firmware's Bluetooth device in Android and pair again. The two integrated firmwares use different Bluetooth addresses and independent bond stores.
- **Bootloader/layout mismatch on update:** use the recovery operation for a compatible table, or a destructive complete installation after backing up.
- **Android browser:** no Android USB flashing configuration has been verified. Browsers without Web Serial are rejected; use a desktop browser or the CLI. Normal Android radio apps remain the intended clients.
- **Flash isolation:** guards protect the integrated firmware's SDK write/reset/format paths. They are not a hardware security boundary against arbitrary native code or external USB tools.
- There is no settings migration from ordinary upstream installations or between incompatible layout/storage versions.

## License and attribution

Coretastic code is GPL-3.0; see [LICENSE](LICENSE). MeshCore is MIT licensed, copyright Scott Powell / rippleradios.com and contributors. Meshtastic is GPL-3.0 licensed. Their notices remain in the pinned submodules. ESP-IDF and esptool-js are Espressif projects; Arduino-ESP32, radio, display, sensor, and other libraries retain their own licenses.

Release source archives include the upstream sources, integration patches, dependency sources, SDK source packages/configuration, and license files. [Hardware sources and integration notes](docs/architecture.md) link the reviewed definitions and schematics.
