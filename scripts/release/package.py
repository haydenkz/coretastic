#!/usr/bin/env python3
"""Assemble a validated release: the selector and every locked app version, per board."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Release tooling imports modules from the sibling scripts/ directories.
_scripts = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_scripts / "device"), str(_scripts / "firmware")]

from layout import (
    APPS,
    MANIFEST_SCHEMA,
    ROOT,
    STORAGE_EPOCH,
    board_file,
    boards,
    flash_size,
    partition,
    partition_binary,
    partitions,
    sha,
    validate_manifest,
)
from prepare import build_dir, environment, input_digest, lock


def one(directory, pattern):
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one {directory}/{pattern}; found {len(paths)}")
    return paths[0]


def image(output, board_id, name, data, offset):
    path = output / board_file(board_id, name)
    path.write_bytes(data)
    return dict(file=path.name, offset=offset, size=len(data), sha256=sha(data))


def package(version, output):
    output.mkdir(parents=True, exist_ok=False)
    locked = lock()
    for component in APPS:
        for record in locked[component]["versions"]:
            directory = build_dir(component, record["version"])
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=directory, text=True
            ).strip()
            if actual != record["commit"]:
                raise ValueError(f"{directory}: source revision differs from lock")
            marker = directory / ".coretastic-inputs"
            if not marker.exists() or marker.read_text() != input_digest(
                component, record["version"]
            ):
                raise ValueError(f"{directory}: integration inputs changed; rebuild first")
    releases = {}
    builds = []  # (metadata label, board, PlatformIO build directory)
    for board_id, profile in boards().items():
        selector_build = ROOT / ".pio/build" / f"selector-{board_id}"
        builds.append((f"{board_id}-selector", board_id, selector_build))
        images = dict(
            selector=image(
                output,
                board_id,
                "selector",
                (selector_build / "firmware.bin").read_bytes(),
                partition(board_id, "selector")["offset"],
            ),
            bootloader=image(
                output, board_id, "bootloader", (selector_build / "bootloader.bin").read_bytes(), 0
            ),
            partitions=image(output, board_id, "partitions", partition_binary(board_id), 0x8000),
        )
        apps = {}
        for component in APPS:
            apps[component] = []
            for record in locked[component]["versions"]:
                firmware = (
                    build_dir(component, record["version"])
                    / ".pio/build"
                    / environment(component, board_id)
                )
                builds.append((f"{board_id}-{component}-{record['version']}", board_id, firmware))
                # Meshtastic names its image after the upstream version; MeshCore does not.
                source = (
                    one(firmware, "firmware-*.elf").with_suffix(".bin")
                    if component == "meshtastic"
                    else firmware / "firmware.bin"
                )
                name = f"{component}-{record['version']}"
                entry = image(
                    output,
                    board_id,
                    name,
                    source.read_bytes(),
                    partition(board_id, component)["offset"],
                )
                apps[component].append(
                    dict(version=record["version"], commit=record["commit"], **entry)
                )
        releases[board_id] = dict(
            name=profile["name"],
            experimental=profile["experimental"],
            layout=profile["layout"],
            flash_size=flash_size(board_id),
            partitions=partitions(board_id),
            images=images,
            apps=apps,
        )
    for label, board_id, directory in builds:
        built = (directory / "partitions.bin").read_bytes().ljust(4096, b"\xff")
        if built != partition_binary(board_id):
            raise ValueError(f"{label}: built partition table differs")
    manifest = dict(
        schema=MANIFEST_SCHEMA,
        version=version,
        chip="ESP32-S3",
        storage_epoch=STORAGE_EPOCH,
        boards=releases,
        repositories={component: locked[component]["repository"] for component in APPS},
        source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
    )
    validate_manifest(manifest, output)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha(p.read_bytes())}  {p.name}\n" for p in sorted(output.iterdir()) if p.is_file()
        )
    )
    metadata = output / "build-metadata"
    metadata.mkdir()
    for label, _, directory in builds:
        for pattern in ["*.elf", "*.map"]:
            for path in directory.glob(pattern):
                shutil.copy2(path, metadata / f"{label}-{path.name}")
    print(f"Validated release {version}: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    package(args.version, args.output)
