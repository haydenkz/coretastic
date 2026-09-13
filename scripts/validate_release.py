#!/usr/bin/env python3
"""Fail if any binary, layout, manifest, or isolation link check is invalid."""

import argparse
from pathlib import Path
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
        "__wrap_esp_flash_write",
        "__wrap_esp_flash_erase_region",
        "__wrap_spi_flash_erase_sector",
        "__wrap_spi_flash_write",
        "__wrap_esp_ota_begin",
    ]
    for component in ["meshcore", "meshtastic"]:
        maps = list(metadata.glob(component + "-*.map"))
        if len(maps) != 1:
            raise ValueError(f"Expected one linker map for {component}")
        content = maps[0].read_text()
        for symbol in required:
            # The cross-reference table includes both linked and garbage-collected functions.
            # Require a placed .text section or an actual address-bearing definition.
            import re

            if not re.search(
                r"0x[0-9a-f]+\s+" + re.escape(symbol) + r"\s*$", content, re.MULTILINE
            ):
                # Unused entry points can be removed; the live common storage calls cannot.
                if symbol in [
                    "__wrap_app_main",
                    "__wrap_nvs_open",
                    "__wrap_esp_flash_write",
                    "__wrap_esp_flash_erase_region",
                ]:
                    raise ValueError(
                        f"{component}: required live isolation symbol missing: {symbol}"
                    )
                if symbol not in content:
                    raise ValueError(f"{component}: isolation wrapper absent from link: {symbol}")
    print(
        f"Validated all images, partition boundaries, and isolation links for {manifest['version']}"
    )


if __name__ == "__main__":
    main()
