#!/usr/bin/env python3
"""Run host tests against selector logic, the actual integration, and USB contracts."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    output = ROOT / ".build/tests"
    output.mkdir(parents=True, exist_ok=True)
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
    for test, flags in [
        ("selector", []),
        ("integration-mc", ["-DCORETASTIC_MESHCORE=1"]),
        ("integration-mt", ["-DCORETASTIC_MESHCORE=0"]),
    ]:
        source = "selector" if test == "selector" else "integration"
        binary = output / test
        run(
            os.environ.get("CXX", "c++"),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Iinclude",
            "-Itests/idf_stubs",
            *flags,
            f"tests/{source}_test.cpp",
            "-o",
            str(binary),
        )
        run(str(binary))


if __name__ == "__main__":
    main()
