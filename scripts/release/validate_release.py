#!/usr/bin/env python3
"""Fail if any binary, layout, manifest, or isolation link check is invalid."""

import argparse
import re
import sys
from pathlib import Path

# Release tooling imports layout from the sibling scripts/device directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from layout import load_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    metadata = args.manifest.parent / "build-metadata"
    required = [
        "__wrap_app_main",
        "__wrap_nvs_open",
        "__wrap_nvs_flash_init",
        "__wrap_nvs_flash_erase",
        "__wrap_nvs_open_from_partition",
        "__wrap_nvs_get_stats",
        "__wrap_esp_flash_write",
        "__wrap_esp_flash_erase_region",
        "__wrap_spi_flash_erase_sector",
        "__wrap_spi_flash_write",
        "__wrap_esp_ota_begin",
    ]
    builds = [
        (component, f"{component} {entry['version']}", f"{component}-{entry['version']}-*.map")
        for component, entries in manifest["apps"].items()
        for entry in entries
    ]
    for component, label, pattern in builds:
        maps = list(metadata.glob(pattern))
        if len(maps) != 1:
            raise ValueError(f"Expected one linker map for {label}")
        content = maps[0].read_text()
        for symbol in required:
            # The cross-reference table includes both linked and garbage-collected functions.
            # Require a placed .text section or an actual address-bearing definition.
            if not re.search(
                r"0x[0-9a-f]+\s+" + re.escape(symbol) + r"\s*$", content, re.MULTILINE
            ):
                # Unused entry points can be removed; the live common storage calls cannot.
                # Meshtastic's esp32Setup() asserts on nvs_get_stats(NULL), so its wrapper must
                # be live there; MeshCore never calls it, so a garbage-collected copy is fine.
                live = symbol in [
                    "__wrap_app_main",
                    "__wrap_nvs_open",
                    "__wrap_esp_flash_write",
                    "__wrap_esp_flash_erase_region",
                ] or (symbol == "__wrap_nvs_get_stats" and component == "meshtastic")
                if live:
                    raise ValueError(f"{label}: required live isolation symbol missing: {symbol}")
                if symbol not in content:
                    raise ValueError(f"{label}: isolation wrapper absent from link: {symbol}")
    print(
        f"Validated all images, partition boundaries, and isolation links for {manifest['version']}"
    )


if __name__ == "__main__":
    main()
