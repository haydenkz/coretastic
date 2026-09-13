#!/usr/bin/env python3
"""Assemble a validated release from the three actual firmware builds."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from layout import (
    BOARDS,
    FLASH_SIZE,
    LAYOUT_ID,
    ROOT,
    partition_binary,
    partitions,
    sha,
    validate_manifest,
)


def one(directory, pattern):
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one {directory}/{pattern}; found {len(paths)}")
    return paths[0]


def package(version, output):
    output.mkdir(parents=True, exist_ok=False)
    lock = json.loads((ROOT / "upstream-lock.json").read_text())
    from prepare import input_digest

    for component, record in lock.items():
        for directory in [ROOT / component / "upstream", ROOT / ".build" / component]:
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=directory, text=True
            ).strip()
            if actual != record["commit"]:
                raise ValueError(f"{directory}: source revision differs from lock")
        marker = ROOT / ".build" / component / ".coretastic-inputs"
        if not marker.exists() or marker.read_text() != input_digest(component):
            raise ValueError(f"{component}: rebuild changed integration inputs before packaging")
    source = {
        "selector": ROOT / ".pio/build/selector/firmware.bin",
        "bootloader": ROOT / ".pio/build/selector/bootloader.bin",
        "meshcore": ROOT / ".build/meshcore/.pio/build/coretastic-meshcore/firmware.bin",
        "meshtastic": one(
            ROOT / ".build/meshtastic/.pio/build/coretastic-meshtastic", "firmware-*.elf"
        ).with_suffix(".bin"),
    }
    for directory in [
        ROOT / ".pio/build/selector",
        ROOT / ".build/meshcore/.pio/build/coretastic-meshcore",
        ROOT / ".build/meshtastic/.pio/build/coretastic-meshtastic",
    ]:
        if (directory / "partitions.bin").read_bytes().ljust(4096, b"\xff") != partition_binary():
            raise ValueError(f"{directory}: built partition table differs")
    images = {}
    offsets = {p["name"]: p["offset"] for p in partitions()}
    offsets.update(bootloader=0, partitions=0x8000)
    for name in [*source, "partitions"]:
        data = partition_binary() if name == "partitions" else source[name].read_bytes()
        path = output / f"{name}.bin"
        path.write_bytes(data)
        images[name] = dict(file=path.name, offset=offsets[name], size=len(data), sha256=sha(data))
    manifest = dict(
        schema=1,
        version=version,
        layout=LAYOUT_ID,
        chip="ESP32-S3",
        flash_size=FLASH_SIZE,
        boards=BOARDS,
        partitions=partitions(),
        storage_epoch=dict(meshcore=1, meshtastic=1, selector=1),
        images=images,
        upstream=lock,
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
    for component, directory in [
        ("selector", ROOT / ".pio/build/selector"),
        ("meshcore", ROOT / ".build/meshcore/.pio/build/coretastic-meshcore"),
        ("meshtastic", ROOT / ".build/meshtastic/.pio/build/coretastic-meshtastic"),
    ]:
        for pattern in ["*.elf", "*.map"]:
            for path in directory.glob(pattern):
                shutil.copy2(path, metadata / f"{component}-{path.name}")
    print(f"Validated release {version}: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    package(args.version, args.output)
