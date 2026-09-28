#!/usr/bin/env python3
"""Build pinned selector and integrated upstream firmware with PlatformIO."""

import argparse
import os
import subprocess
import shutil

from prepare import ROOT, build_dir, input_digest, lock, prepare


def remove_generated(directory, component):
    origin = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=directory, text=True
    ).strip()
    if origin != str(ROOT / component / "upstream"):
        raise ValueError(f"Refusing to replace unexpected checkout: {directory}")
    shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "component", choices=["all", "selector", "meshcore", "meshtastic"], default="all", nargs="?"
    )
    parser.add_argument(
        "--version", help="Build one locked app version instead of every locked version"
    )
    args = parser.parse_args()
    components = (
        ["selector", "meshcore", "meshtastic"] if args.component == "all" else [args.component]
    )
    if args.version and args.component in ("all", "selector"):
        parser.error("--version needs meshcore or meshtastic")
    locked = lock()
    for component in components:
        if component == "selector":
            targets = [(ROOT, "selector")]
        else:
            # Checkouts made before per-version builds sit directly in .build/<component>.
            legacy = ROOT / ".build" / component
            if (legacy / ".git").exists():
                print(f"Replacing single-version checkout {legacy}", flush=True)
                remove_generated(legacy, component)
            versions = [entry["version"] for entry in locked[component]["versions"]]
            if args.version:
                if args.version not in versions:
                    raise ValueError(f"{component} {args.version} is not in upstream-lock.json")
                versions = [args.version]
            targets = []
            for version in versions:
                directory = build_dir(component, version)
                marker = directory / ".coretastic-inputs"
                if directory.exists() and (
                    not marker.exists() or marker.read_text() != input_digest(component, version)
                ):
                    print(f"Integration changed: recreating {directory}", flush=True)
                    remove_generated(directory, component)
                if not directory.exists():
                    prepare(component, version)
                targets.append((directory, locked[component]["environment"]))
        for directory, environment in targets:
            command = [os.environ.get("PIO", "pio"), "run", "-d", str(directory), "-e", environment]
            subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
