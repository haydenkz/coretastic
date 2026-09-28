#!/usr/bin/env python3
"""Create disposable, pinned build checkouts; never patch the source submodules."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/device"))

from layout import boards, layout_header, partition  # noqa: E402


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


def environment(component, board_id):
    return f"{lock()[component]['environment']}-{board_id}"


def board_environment(component, board_id):
    """One PlatformIO environment per board, extending its upstream environment."""
    upstream = boards()[board_id]["environments"][component]
    return f"""
[env:{environment(component, board_id)}]
extends = env:{upstream}
custom_coretastic_board = {board_id}
board_build.partitions = coretastic/boards/{board_id}/partitions.csv
board_build.app_partition_name = {component}
board_upload.offset_address = {partition(board_id, component)["offset"]:#x}
extra_scripts = ${{coretastic.extra_scripts}}
build_flags =
    ${{env:{upstream}.build_flags}}
    ${{coretastic.build_flags}}
lib_deps = ${{coretastic.lib_deps}}
platform_packages = ${{coretastic.platform_packages}}
"""


def generated_files(component, version):
    """Everything prepare writes into a checkout besides patches, by relative path."""
    firmware = ROOT / "scripts/firmware"
    files = {
        "coretastic/integration.cpp": (firmware / "integration.cpp").read_bytes(),
        "coretastic/storage_boundary.h": (ROOT / "include/storage_boundary.h").read_bytes(),
        "coretastic/build.py": (firmware / "pio_integration.py").read_bytes(),
    }
    config = (config_dir(component, version) / "integration.ini").read_text()
    for board_id in boards():
        board_files = ROOT / "boards" / board_id
        files[f"coretastic/boards/{board_id}/partitions.csv"] = (
            board_files / "partitions.csv"
        ).read_bytes()
        files[f"coretastic/boards/{board_id}/coretastic_layout.h"] = layout_header(
            board_id
        ).encode()
        config += board_environment(component, board_id)
    return files, config


def input_digest(component, version):
    files, config = generated_files(component, version)
    digest = hashlib.sha256(json.dumps(locked_version(component, version)).encode())
    patches = sorted((config_dir(component, version) / "patches").glob("*.patch"))
    for name, data in [
        *sorted(files.items()),
        ("platformio.ini", config.encode()),
        *((str(path.relative_to(ROOT)), path.read_bytes()) for path in patches),
    ]:
        digest.update(name.encode())
        digest.update(data)
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
    files, platformio = generated_files(component, version)
    for name, data in files.items():
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(data)
    with (dest / "platformio.ini").open("a") as ini:
        ini.write(platformio)
    (dest / ".coretastic-inputs").write_text(input_digest(component, version))
    print(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["meshcore", "meshtastic"])
    parser.add_argument("version", help="A version listed in upstream-lock.json")
    args = parser.parse_args()
    prepare(args.component, args.version)
