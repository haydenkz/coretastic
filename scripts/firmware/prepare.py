#!/usr/bin/env python3
"""Create disposable, pinned build checkouts; never patch the source submodules."""

import argparse
import json
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def input_digest(component):
    files = [
        ROOT / "upstream-lock.json",
        ROOT / "partitions.csv",
        ROOT / "scripts/firmware/integration.cpp",
        ROOT / "scripts/firmware/pio_integration.py",
        ROOT / "include/storage_boundary.h",
        ROOT / component / "integration.ini",
    ]
    files.extend(sorted((ROOT / component / "patches").glob("*.patch")))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare(component):
    lock = json.loads((ROOT / "upstream-lock.json").read_text())[component]
    upstream = ROOT / component / "upstream"
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    if actual != lock["commit"]:
        raise ValueError(f"{component}: expected {lock['commit']}, found {actual}")
    dest = ROOT / ".build" / component
    if dest.exists():
        raise ValueError(
            f"{dest} already exists; remove this generated checkout before preparing again"
        )
    dest.parent.mkdir(exist_ok=True)
    run("git", "clone", "--shared", "--no-checkout", str(upstream), str(dest))
    run("git", "checkout", "--detach", lock["commit"], cwd=dest)
    run("git", "submodule", "update", "--init", "--recursive", cwd=dest)
    for patch in sorted((ROOT / component / "patches").glob("*.patch")):
        run("git", "apply", "--check", str(patch), cwd=dest)
        run("git", "apply", str(patch), cwd=dest)
    integration = dest / "coretastic"
    integration.mkdir()
    for source in [ROOT / "scripts/firmware/integration.cpp", ROOT / "include/storage_boundary.h"]:
        shutil.copy2(source, integration / source.name)
    shutil.copy2(ROOT / "scripts/firmware/pio_integration.py", integration / "build.py")
    shutil.copy2(ROOT / "partitions.csv", integration / "partitions.csv")
    with (dest / "platformio.ini").open("a") as config:
        config.write((ROOT / component / "integration.ini").read_text())
    (dest / ".coretastic-inputs").write_text(input_digest(component))
    print(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["meshcore", "meshtastic"])
    prepare(parser.parse_args().component)
