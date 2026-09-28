# Changelog

## Unreleased

- Choose the MeshCore and Meshtastic version when installing or updating, in the web flasher and the CLI (`--meshcore-version`, `--meshtastic-version`). Releases offer MeshCore `companion-v1.17.1`, `companion-v1.17.0`, and `companion-v1.16.0`, and Meshtastic `v2.7.26.54e0d8d` (the only stable release that supports the V4.3 LoRa front end).
- The flasher records each app's installed version and requires erasing that app's settings before installing an older version.
- Experimental Heltec WiFi LoRa 32 V3 support: an 8 MiB dual-boot layout, a V3 selector build, per-board app builds, and CP2102 USB in the flasher. It is hidden unless you opt in (web: *Show experimental boards*; CLI: `--experimental`) because it has not been tested on hardware.
- Boards are defined in `boards/<id>/` (profile, partition table, selector SDK settings); the flash write guard's bounds are generated per board.
- Release manifest schema 3 lists images per board and every app version; files are named `<board>-<image>.bin`. The corresponding-source archive bundles each version's sources.

## v0.1.0 - 2026-09-14

- Dual-boot selector for MeshCore `companion-v1.17.0` and Meshtastic `v2.7.26.54e0d8d` on one Heltec V4 OLED board.
- Web flasher and Python CLI: install, component update, full backup and restore, recovery.
- Selector marks are the bare logo glyphs — MeshCore's own logo and the Meshtastic bolt, no badge background.
- Brand header generated deterministically from the shipped web artwork (Pillow-based PNG pipeline).
- Repository history squashed to a single initial commit; CI, Pages workflows, and the previous release removed.
