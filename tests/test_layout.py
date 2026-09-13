import copy
import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from layout import (
    BOARDS,
    FLASH_SIZE,
    LAYOUT_ID,
    partition_binary,
    partitions,
    sha,
    validate_image,
    validate_manifest,
)


def image_fixture():
    header = bytearray(24)
    header[0:4] = bytes([0xE9, 1, 2, 0x4F])
    struct.pack_into("<H", header, 12, 9)
    header[23] = 1
    payload = struct.pack("<I", 0xABCD5432) + bytes(252)
    image = header + struct.pack("<II", 0x3C000020, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image += bytes(15) + bytes([checksum])
    return bytes(image + hashlib.sha256(image).digest())


def manifest_fixture():
    images = {
        p["name"]: dict(file=p["name"] + ".bin", offset=p["offset"], size=336, sha256="a" * 64)
        for p in partitions()
        if p["type"] == "app"
    }
    images["bootloader"] = dict(file="bootloader.bin", offset=0, size=336, sha256="a" * 64)
    images["partitions"] = dict(
        file="partitions.bin", offset=0x8000, size=4096, sha256=sha(partition_binary())
    )
    return dict(
        schema=1,
        layout=LAYOUT_ID,
        version="test",
        chip="ESP32-S3",
        flash_size=FLASH_SIZE,
        boards=BOARDS,
        partitions=partitions(),
        images=images,
        storage_epoch=dict(meshcore=1, meshtastic=1, selector=1),
    )


class LayoutTests(unittest.TestCase):
    def test_dependency_locks_match_build_configuration(self):
        import configparser
        import json

        root = Path(__file__).resolve().parents[1]
        for component in ("meshcore", "meshtastic"):
            config = configparser.ConfigParser(interpolation=None)
            config.read(root / component / "integration.ini")
            dependencies = config.get("env:coretastic-" + component, "lib_deps").splitlines()
            self.assertEqual(
                [line.strip() for line in dependencies if line.strip()],
                json.loads((root / component / "dependencies.json").read_text()),
            )

    def test_image_integrity(self):
        data = image_fixture()
        validate_image(data)
        for position in (0, 12, 40, 303, 320):
            broken = bytearray(data)
            broken[position] ^= 1
            with self.assertRaises(ValueError):
                validate_image(broken)
        for cut in (0, 23, 28, 100, 303, 335):
            with self.assertRaises(ValueError):
                validate_image(data[:cut])

    def test_manifest_rejects_unsafe_changes(self):
        manifest = manifest_fixture()
        validate_manifest(manifest)
        for component in manifest["images"]:
            for key, value in (
                ("offset", 0x9000),
                ("size", 0x1000001),
                ("file", "../x"),
                ("sha256", "bad"),
            ):
                broken = copy.deepcopy(manifest)
                broken["images"][component][key] = value
                with self.assertRaises(ValueError):
                    validate_manifest(broken)
        broken = copy.deepcopy(manifest)
        broken["partitions"][3]["offset"] = 0x10000
        with self.assertRaises(ValueError):
            validate_manifest(broken)

    def test_partition_table_md5(self):
        table = partition_binary()
        self.assertEqual(len(table), 4096)
        end = len(partitions()) * 32
        self.assertEqual(table[end : end + 2], b"\xeb\xeb")
        self.assertEqual(table[end + 16 : end + 32], hashlib.md5(table[:end]).digest())


if __name__ == "__main__":
    unittest.main()
