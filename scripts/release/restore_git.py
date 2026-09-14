#!/usr/bin/env python3
"""Recreate buildable Git checkouts from an extracted corresponding-source archive."""

import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Extracted directory containing bundles/"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New, nonexistent checkout directory"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; choose a new directory")
    repositories = {
        "coretastic": "",
        "meshcore": "meshcore/upstream",
        "meshtastic": "meshtastic/upstream",
        "protobufs": "meshtastic/upstream/protobufs",
        "meshtestic": "meshtastic/upstream/meshtestic",
    }
    for name in repositories:
        bundle = args.source / "bundles" / f"{name}.bundle"
        if not bundle.is_file():
            raise ValueError(f"Missing source bundle: {bundle}")
    for name, relative in repositories.items():
        subprocess.run(
            [
                "git",
                "clone",
                str((args.source / "bundles" / f"{name}.bundle").resolve()),
                str(args.output / relative),
            ],
            check=True,
        )
    print(
        f"Restored Git history and pinned checkouts in {args.output}; follow README build commands"
    )


if __name__ == "__main__":
    main()
