#!/usr/bin/env python3
"""Archive corresponding source, dependency sources, licenses, and SDK configuration."""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Release tooling imports modules from the sibling scripts/ directories.
_scripts = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_scripts / "device"), str(_scripts / "firmware")]

from layout import APPS, ROOT, sha
from prepare import build_dir, lock

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
        # Clean bundles retain commit identities needed by the build/version checks,
        # without copying local Git config, credentials, hooks, or reflogs. The index
        # tells restore_git.py where each bundle belongs; every app version ships its
        # own upstream and nested submodule commits.
        repositories = [("coretastic", ROOT, "", None)]
        locked = lock()
        for component in APPS:
            for record in locked[component]["versions"]:
                version = record["version"]
                checkout = build_dir(component, version)
                ref = f"refs/coretastic/{version}"
                name = f"{component}-{version}"
                repositories.append((name, checkout, f"{component}/upstream", ref))
                nested = subprocess.check_output(
                    ["git", "submodule", "--quiet", "foreach", "--recursive", "echo $displaypath"],
                    cwd=checkout,
                    text=True,
                ).split()
                for path in nested:
                    repositories.append(
                        (
                            f"{name}-{path.replace('/', '_')}",
                            checkout / path,
                            f"{component}/upstream/{path}",
                            ref,
                        )
                    )
        index = []
        with tempfile.TemporaryDirectory(prefix="coretastic-source-") as temporary:
            for name, repository, path, ref in repositories:
                bundle = Path(temporary) / f"{name}.bundle"
                subprocess.run(
                    ["git", "bundle", "create", str(bundle), "HEAD"], cwd=repository, check=True
                )
                subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=repository, check=True)
                tar.add(bundle, arcname=f"bundles/{name}.bundle")
                index.append(dict(bundle=f"{name}.bundle", path=path, ref=ref))
            index_file = Path(temporary) / "index.json"
            index_file.write_text(json.dumps(index, indent=2) + "\n")
            tar.add(index_file, arcname="bundles/index.json")
        for component in APPS:
            environment = locked[component]["environment"]
            for record in locked[component]["versions"]:
                version = record["version"]
                deps = build_dir(component, version) / ".pio/libdeps" / environment
                if not deps.is_dir():
                    raise ValueError(f"Missing built dependency sources: {deps}")
                tar.add(deps, arcname=f"dependencies/{component}-{version}", filter=source_filter)
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
