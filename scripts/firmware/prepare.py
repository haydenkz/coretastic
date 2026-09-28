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


def lock():
    return json.loads((ROOT / "upstream-lock.json").read_text())


def locked_version(component, version):
    for entry in lock()[component]["versions"]:
        if entry["version"] == version:
            return entry
    raise ValueError(f"{component} {version} is not in upstream-lock.json")


def config_dir(component, version):
    return ROOT / component / "versions" / version


def build_dir(component, version):
    return ROOT / ".build" / component / version


def input_digest(component, version):
    config = config_dir(component, version)
    files = [
        ROOT / "partitions.csv",
        ROOT / "scripts/firmware/integration.cpp",
        ROOT / "scripts/firmware/pio_integration.py",
        ROOT / "include/storage_boundary.h",
        config / "integration.ini",
    ]
    files.extend(sorted((config / "patches").glob("*.patch")))
    digest = hashlib.sha256(json.dumps(locked_version(component, version)).encode())
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def fetch_commit(component, version):
    """Makes the pinned commit available in the submodule's object store."""
    upstream = ROOT / component / "upstream"
    commit = locked_version(component, version)["commit"]
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=upstream, capture_output=True
    )
    if present.returncode != 0:
        repository = lock()[component]["repository"]
        # A ref keeps the fetched commit from being garbage collected.
        ref = f"+{commit}:refs/coretastic/{version}"
        run("git", "fetch", "--no-tags", repository, ref, cwd=upstream)
    return commit


def prepare(component, version):
    commit = fetch_commit(component, version)
    config = config_dir(component, version)
    if not (config / "integration.ini").is_file():
        raise ValueError(f"{config}: missing integration.ini for {component} {version}")
    dest = build_dir(component, version)
    if dest.exists():
        raise ValueError(
            f"{dest} already exists; remove this generated checkout before preparing again"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    run("git", "clone", "--shared", "--no-checkout", str(ROOT / component / "upstream"), str(dest))
    run("git", "checkout", "--detach", commit, cwd=dest)
    run("git", "submodule", "update", "--init", "--recursive", cwd=dest)
    for patch in sorted((config / "patches").glob("*.patch")):
        run("git", "apply", "--check", str(patch), cwd=dest)
        run("git", "apply", str(patch), cwd=dest)
    integration = dest / "coretastic"
    integration.mkdir()
    for source in [ROOT / "scripts/firmware/integration.cpp", ROOT / "include/storage_boundary.h"]:
        shutil.copy2(source, integration / source.name)
    shutil.copy2(ROOT / "scripts/firmware/pio_integration.py", integration / "build.py")
    shutil.copy2(ROOT / "partitions.csv", integration / "partitions.csv")
    with (dest / "platformio.ini").open("a") as platformio:
        platformio.write((config / "integration.ini").read_text())
    (dest / ".coretastic-inputs").write_text(input_digest(component, version))
    print(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["meshcore", "meshtastic"])
    parser.add_argument("version", help="A version listed in upstream-lock.json")
    args = parser.parse_args()
    prepare(args.component, args.version)
