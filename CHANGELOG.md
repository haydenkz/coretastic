# Changelog

## v0.2.1 - 2026-10-18

- Adds MeshCore `companion-v1.18.0`. This release offers MeshCore `companion-v1.18.0`, `companion-v1.17.1`, and `companion-v1.17.0` (`companion-v1.16.0` is no longer offered), and Meshtastic `v2.7.26.54e0d8d`.
- The web flasher works at <https://haydenkz.github.io/coretastic/>. It was built with absolute asset URLs, which only work at a domain root, so the page loaded without its script and styles.
- Flasher fixes:
  - Both flashers erase settings before writing an app and write its version record last, so a retry after an interrupted downgrade asks to erase again.
  - The web flasher checks every flash read against the device's MD5, as the CLI already did, so a corrupted backup read fails instead of being saved.
  - A verified operation is no longer reported as failed when the follow-up read of installed versions fails.
  - The progress bar reaches 100%.
  - Toggling experimental boards keeps the chosen app versions.
- Selector fixes:
  - Short PRG taps register. The OLED is redrawn only when the screen changes, at 400 kHz, so the button is sampled often enough.
  - The selector releases the GPIO2 and GPIO13 (LoRa BUSY) holds that Meshtastic sets for deep sleep.
- The corresponding-source archive includes the SDK `sdkconfig.h` headers and nested npm packages it used to drop, and takes only tracked files from this repository.
- Upstream sync fixes:
  - A version whose pull request couldn't be opened is no longer skipped without an error.
  - Closing a sync pull request now declines its versions permanently.
- Workflows use Node 24 actions.
- The Python tooling uses urllib3 2.8.0.

## v0.2.0 - 2026-09-28

- Choose the MeshCore and Meshtastic version when installing or updating, in the web flasher and the CLI (`--meshcore-version`, `--meshtastic-version`). This release offers MeshCore `companion-v1.17.1`, `companion-v1.17.0`, and `companion-v1.16.0`, and Meshtastic `v2.7.26.54e0d8d` (the only stable release that supports the V4.3 LoRa front end).
- The flasher records each app's installed version and requires erasing that app's settings before installing an older version.
- Experimental Heltec WiFi LoRa 32 V3 support: an 8 MiB dual-boot layout, a V3 selector build, per-board app builds, and CP2102 USB in the flasher. It is hidden unless you opt in (web: *Show experimental boards*; CLI: `--experimental`) because it has not been tested on hardware.
- Flasher hardening: the CLI verifies writes with an on-device MD5 instead of reading flash back, refuses an existing backup file before the full read, and reports malformed backups clearly; the web flasher bypasses the HTTP cache for release files. The selector shows its error screen instead of reboot-looping when the button or NVS setup fails.
- A daily upstream sync workflow opens a pull request for each new stable MeshCore or Meshtastic release, with the version locked, its configuration seeded, patches and build settings checked, and every board built.
- Boards are defined in `boards/<id>/` (profile, partition table, selector SDK settings); the flash write guard's bounds are generated per board.
- Release manifest schema 3 lists images per board and every app version; files are named `<board>-<image>.bin`. Use the flasher from the same release. The corresponding-source archive bundles each version's sources.
- The release workflow grants write access only to the jobs that need it and deploys GitHub Pages only for tagged releases.

## v0.1.0 - 2026-09-14

- Dual-boot selector for MeshCore `companion-v1.17.0` and Meshtastic `v2.7.26.54e0d8d` on one Heltec V4 OLED board.
- Web flasher and Python CLI: install, component update, full backup and restore, recovery.
- Selector marks are the bare logo glyphs — MeshCore's own logo and the Meshtastic bolt, no badge background.
- Brand header generated deterministically from the shipped web artwork (Pillow-based PNG pipeline).
- Repository history squashed to a single initial commit; CI, Pages workflows, and the previous release removed.
