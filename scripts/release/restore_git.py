#!/usr/bin/env python3
"""Recreate buildable Git checkouts from an extracted corresponding-source archive."""

import argparse
import json
import subprocess
from pathlib import Path


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


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
    bundles = args.source / "bundles"
    index = json.loads((bundles / "index.json").read_text())
    for entry in index:
        if not (bundles / entry["bundle"]).is_file():
            raise ValueError(f"Missing source bundle: {bundles / entry['bundle']}")
    root = next(entry for entry in index if entry["path"] == "")
    subprocess.run(
        ["git", "clone", str((bundles / root["bundle"]).resolve()), str(args.output)], check=True
    )
    # Every app version's commits go into one repository per path, under the same
    # refs prepare.py uses, so builds find them without fetching upstream.
    paths = []
    for entry in index:
        if entry["path"] == "":
            continue
        repository = args.output / entry["path"]
        if entry["path"] not in paths:
            paths.append(entry["path"])
            repository.mkdir(parents=True, exist_ok=True)
            git("init", "--quiet", cwd=repository)
        bundle = str((bundles / entry["bundle"]).resolve())
        git("fetch", "--quiet", bundle, f"+HEAD:{entry['ref']}", cwd=repository)
    # Check out what each parent repository pins, outermost first.
    for path in sorted(paths, key=lambda value: value.count("/")):
        parent = next(
            (
                other
                for other in sorted(paths, key=len, reverse=True)
                if path.startswith(other + "/")
            ),
            "",
        )
        relative = path[len(parent) + 1 :] if parent else path
        pinned = git("ls-tree", "HEAD", relative, cwd=args.output / parent).stdout.split()
        if len(pinned) < 3 or pinned[1] != "commit":
            raise ValueError(f"{parent or 'coretastic'} does not pin a submodule at {relative}")
        git("checkout", "--quiet", "--detach", pinned[2], cwd=args.output / path)
    print(
        f"Restored Git history and pinned checkouts in {args.output}; follow README build commands"
    )


if __name__ == "__main__":
    main()
