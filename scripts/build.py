#!/usr/bin/env python3
"""Build pinned selector and integrated upstream firmware with PlatformIO."""

import argparse
import os
import subprocess
import shutil
from prepare import ROOT, prepare, input_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "component", choices=["all", "selector", "meshcore", "meshtastic"], default="all", nargs="?"
    )
    args = parser.parse_args()
    components = (
        ["selector", "meshcore", "meshtastic"] if args.component == "all" else [args.component]
    )
    for component in components:
        if component == "selector":
            directory, environment = ROOT, "selector"
        else:
            directory, environment = ROOT / ".build" / component, "coretastic-" + component
            marker = directory / ".coretastic-inputs"
            if directory.exists() and (
                not marker.exists() or marker.read_text() != input_digest(component)
            ):
                origin = subprocess.check_output(
                    ["git", "remote", "get-url", "origin"], cwd=directory, text=True
                ).strip()
                if origin != str(ROOT / component / "upstream"):
                    raise ValueError(f"Refusing to replace unexpected checkout: {directory}")
                print(f"Integration changed: recreating generated checkout {directory}", flush=True)
                shutil.rmtree(directory)
            if not directory.exists():
                prepare(component)
        command = [os.environ.get("PIO", "pio"), "run", "-d", str(directory), "-e", environment]
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
