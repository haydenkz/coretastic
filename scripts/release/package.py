#!/usr/bin/env python3
"""Assemble a validated release from the selector and every locked app version build."""

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
    BOARDS,
    FLASH_SIZE,
    LAYOUT_ID,
    MANIFEST_SCHEMA,
    ROOT,
    STORAGE_EPOCH,
    partition,
    partition_binary,
    partitions,
    sha,
    validate_manifest,
)
from prepare import build_dir, input_digest, lock


def one(directory, pattern):
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one {directory}/{pattern}; found {len(paths)}")
    return paths[0]


def package(version, output):
    output.mkdir(parents=True, exist_ok=False)
    locked = lock()
    selector_build = ROOT / ".pio/build/selector"
    builds = [("selector", selector_build)]
    apps = {}
    for component in APPS:
        environment = locked[component]["environment"]
        apps[component] = []
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
            firmware = directory / ".pio/build" / environment
            builds.append((f"{component}-{record['version']}", firmware))
            apps[component].append((record, firmware))
    for label, directory in builds:
        if (directory / "partitions.bin").read_bytes().ljust(4096, b"\xff") != partition_binary():
            raise ValueError(f"{label}: built partition table differs")
    images = {}
    shared = {
        "selector": (selector_build / "firmware.bin", partition("selector")["offset"]),
        "bootloader": (selector_build / "bootloader.bin", 0),
        "partitions": (None, 0x8000),
    }
    for name, (source, offset) in shared.items():
        data = partition_binary() if source is None else source.read_bytes()
        path = output / f"{name}.bin"
        path.write_bytes(data)
        images[name] = dict(file=path.name, offset=offset, size=len(data), sha256=sha(data))
    manifest_apps = {}
    for component, entries in apps.items():
        manifest_apps[component] = []
        for record, firmware in entries:
            # Meshtastic names its image after the upstream version; MeshCore does not.
            source = (
                one(firmware, "firmware-*.elf").with_suffix(".bin")
                if component == "meshtastic"
                else firmware / "firmware.bin"
            )
            data = source.read_bytes()
            path = output / f"{component}-{record['version']}.bin"
            path.write_bytes(data)
            manifest_apps[component].append(
                dict(
                    version=record["version"],
                    commit=record["commit"],
                    file=path.name,
                    offset=partition(component)["offset"],
                    size=len(data),
                    sha256=sha(data),
                )
            )
    manifest = dict(
        schema=MANIFEST_SCHEMA,
        version=version,
        layout=LAYOUT_ID,
        chip="ESP32-S3",
        flash_size=FLASH_SIZE,
        boards=BOARDS,
        partitions=partitions(),
        storage_epoch=STORAGE_EPOCH,
        images=images,
        apps=manifest_apps,
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
    for label, directory in builds:
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
