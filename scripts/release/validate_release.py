#!/usr/bin/env python3
"""Fail if any binary, layout, manifest, or isolation link check is invalid."""

import argparse
import re
import sys
from pathlib import Path

# Release tooling imports modules from the sibling scripts/ directories.
_scripts = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_scripts / "device"), str(_scripts / "firmware")]

from layout import boards, load_manifest
from prepare import build_dir, environment

REQUIRED = [
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


def check_map(component, label, maps):
    """Requires the isolation wrappers in one app build's linker map."""
    if len(maps) != 1:
        raise ValueError(f"Expected one linker map for {label}")
    content = maps[0].read_text()
    for symbol in REQUIRED:
        # The cross-reference table includes both linked and garbage-collected functions.
        # Require a placed .text section or an actual address-bearing definition.
        if not re.search(r"0x[0-9a-f]+\s+" + re.escape(symbol) + r"\s*$", content, re.MULTILINE):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("manifest", type=Path, nargs="?", help="A packaged release manifest")
    target.add_argument(
        "--build",
        nargs=2,
        metavar=("COMPONENT", "VERSION"),
        help="Check one app version's builds in .build/ for every board, before packaging",
    )
    args = parser.parse_args()
    if args.build:
        component, version = args.build
        for board_id in boards():
            directory = (
                build_dir(component, version) / ".pio/build" / environment(component, board_id)
            )
            check_map(component, f"{board_id} {component} {version}", list(directory.glob("*.map")))
        print(f"Validated isolation links for {component} {version} on {', '.join(boards())}")
        return
    manifest = load_manifest(args.manifest)
    metadata = args.manifest.parent / "build-metadata"
    for board_id, release in manifest["boards"].items():
        for component, entries in release["apps"].items():
            for entry in entries:
                label = f"{board_id} {component} {entry['version']}"
                pattern = f"{board_id}-{component}-{entry['version']}-*.map"
                check_map(component, label, list(metadata.glob(pattern)))
    print(
        f"Validated all images, partition boundaries, and isolation links for {manifest['version']}"
    )


if __name__ == "__main__":
    main()
