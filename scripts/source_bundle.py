#!/usr/bin/env python3
"""Archive corresponding source, dependency sources, licenses, and SDK configuration."""

import argparse
import json
import os
import subprocess
import tarfile
from pathlib import Path
from layout import ROOT, sha

EXCLUDED = {
    ".git",
    ".cache",
    ".build",
    ".pio",
    ".venv",
    "node_modules",
    "__pycache__",
    "release",
    "dist",
    "releases",
}


def source_filter(info):
    if any(part in EXCLUDED for part in Path(info.name).parts):
        return None
    if (
        Path(info.name).name.startswith("sdkconfig.")
        and Path(info.name).name != "sdkconfig.defaults"
    ):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    archive = args.release / "corresponding-source.tar.gz"
    packages = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio")) / "packages"
    # These source packages include the ESP-IDF submodules and Arduino SDK configurations.
    framework_paths = [packages / "framework-espidf", packages / "framework-arduinoespressif32"]
    for directory in framework_paths:
        if not (directory / "package.json").is_file():
            raise ValueError(f"Required SDK source package missing: {directory}")
    inventory = {}
    with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
        tar.add(ROOT, arcname="coretastic", filter=source_filter)
        for component in ["meshcore", "meshtastic"]:
            deps = ROOT / ".build" / component / ".pio/libdeps" / f"coretastic-{component}"
            if not deps.is_dir():
                raise ValueError(f"Missing built dependency sources: {deps}")
            tar.add(deps, arcname=f"dependencies/{component}", filter=source_filter)
        web_packages = subprocess.check_output(
            ["npm", "ls", "--omit=dev", "--parseable", "--all"], cwd=ROOT / "web", text=True
        ).splitlines()
        for entry in web_packages:
            directory = Path(entry)
            if directory == ROOT / "web":
                continue
            relative = directory.relative_to(ROOT / "web/node_modules")
            tar.add(directory, arcname=f"dependencies/web/{relative}", filter=source_filter)
        for directory in framework_paths:
            tar.add(directory, arcname=f"sdk/{directory.name}", filter=source_filter)
            inventory[directory.name] = json.loads((directory / "package.json").read_text())[
                "version"
            ]
    (args.release / "toolchains.json").write_text(json.dumps(inventory, indent=2) + "\n")
    checksums = args.release / "SHA256SUMS"
    checksums.write_text(
        "".join(
            f"{sha(path.read_bytes())}  {path.name}\n"
            for path in sorted(args.release.iterdir())
            if path.is_file() and path != checksums
        )
    )
    print(f"Source archive: {archive}")


if __name__ == "__main__":
    main()
