import copy
import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "device"))
from layout import (
    BOARDS,
    FLASH_SIZE,
    LAYOUT_ID,
    MANIFEST_SCHEMA,
    SECTOR,
    STORAGE_EPOCH,
    app_record,
    installed_version,
    partition,
    partition_binary,
    partitions,
    record_offset,
    settings_erase_required,
    sha,
    validate_image,
    validate_manifest,
    version_key,
)

MESHCORE_VERSIONS = ["companion-v1.17.1", "companion-v1.16.0"]


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


def app_entry(component, version):
    return dict(
        version=version,
        commit="c" * 40,
        file=f"{component}-{version}.bin",
        offset=partition(component)["offset"],
        size=336,
        sha256="a" * 64,
    )


def manifest_fixture():
    images = dict(
        selector=dict(file="selector.bin", offset=0x10000, size=336, sha256="a" * 64),
        bootloader=dict(file="bootloader.bin", offset=0, size=336, sha256="a" * 64),
        partitions=dict(
            file="partitions.bin", offset=0x8000, size=4096, sha256=sha(partition_binary())
        ),
    )
    return dict(
        schema=MANIFEST_SCHEMA,
        layout=LAYOUT_ID,
        version="test",
        chip="ESP32-S3",
        flash_size=FLASH_SIZE,
        boards=BOARDS,
        partitions=partitions(),
        images=images,
        apps=dict(
            meshcore=[app_entry("meshcore", version) for version in MESHCORE_VERSIONS],
            meshtastic=[app_entry("meshtastic", "v2.7.26.54e0d8d")],
        ),
        storage_epoch=dict(STORAGE_EPOCH),
    )


class LayoutTests(unittest.TestCase):
    def test_dependency_locks_match_build_configuration(self):
        import configparser
        import json

        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root / "upstream-lock.json").read_text())
        for component in ("meshcore", "meshtastic"):
            versions = [entry["version"] for entry in lock[component]["versions"]]
            # Every locked version has its own configuration, and nothing else does.
            configured = sorted(p.name for p in (root / component / "versions").iterdir())
            self.assertEqual(configured, sorted(versions))
            self.assertEqual(
                [version_key(v) for v in versions],
                sorted((version_key(v) for v in versions), reverse=True),
                "upstream-lock.json lists versions newest first",
            )
            for version in versions:
                directory = root / component / "versions" / version
                self.assertTrue(list((directory / "patches").glob("*.patch")), directory)
                config = configparser.ConfigParser(interpolation=None)
                config.read(directory / "integration.ini")
                environment = lock[component]["environment"]
                dependencies = config.get("env:" + environment, "lib_deps").splitlines()
                self.assertEqual(
                    [line.strip() for line in dependencies if line.strip()],
                    json.loads((directory / "dependencies.json").read_text()),
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
        targets = [("images", name) for name in manifest["images"]]
        targets += [("apps", component, 0) for component in manifest["apps"]]
        for target in targets:
            for key, value in (
                ("offset", 0x9000),
                ("size", 0x1000001),
                ("file", "../x"),
                ("sha256", "bad"),
            ):
                broken = copy.deepcopy(manifest)
                image = broken
                for part in target:
                    image = image[part]
                image[key] = value
                with self.assertRaises(ValueError):
                    validate_manifest(broken)
        broken = copy.deepcopy(manifest)
        broken["partitions"][3]["offset"] = 0x10000
        with self.assertRaises(ValueError):
            validate_manifest(broken)

    def test_manifest_app_versions(self):
        manifest = manifest_fixture()
        # An app image may not reach into the sector that holds its version record.
        broken = copy.deepcopy(manifest)
        broken["apps"]["meshcore"][0]["size"] = partition("meshcore")["size"] - SECTOR + 1
        with self.assertRaisesRegex(ValueError, "boundary"):
            validate_manifest(broken)
        for mutate, message in (
            (lambda apps: apps["meshcore"].reverse(), "newest first"),
            (lambda apps: apps["meshcore"].append(apps["meshcore"][0]), "newest first"),
            (lambda apps: apps["meshcore"].clear(), "no versions"),
            (lambda apps: apps.pop("meshtastic"), "unexpected app"),
            (lambda apps: apps["meshcore"][0].update(version="latest"), "unorderable"),
            (lambda apps: apps["meshcore"][0].update(version="../1.2.3"), "invalid version"),
            (lambda apps: apps["meshcore"][0].update(commit="main"), "invalid commit"),
        ):
            broken = copy.deepcopy(manifest)
            mutate(broken["apps"])
            with self.assertRaisesRegex(ValueError, message):
                validate_manifest(broken)
        broken = copy.deepcopy(manifest)
        broken["schema"] = 1
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_manifest(broken)

    def test_version_records_and_downgrades(self):
        self.assertEqual(version_key("companion-v1.17.1"), (1, 17, 1))
        self.assertEqual(version_key("v2.7.26.54e0d8d"), (2, 7, 26))
        self.assertIsNone(version_key("latest"))
        self.assertEqual(record_offset("meshcore"), 0x3FF000)
        self.assertEqual(record_offset("meshtastic"), 0x9FF000)
        record = app_record("meshcore", "companion-v1.17.1", "a" * 64)
        self.assertEqual(len(record), SECTOR)
        self.assertEqual(installed_version("meshcore", record), "companion-v1.17.1")
        # A record for the other app, or garbage, cannot identify the install.
        self.assertIsNone(installed_version("meshtastic", record))
        self.assertIsNone(installed_version("meshcore", b"\0" + record[1:]))
        # v0.1.0 wrote no record and shipped exactly one version of each app.
        self.assertEqual(installed_version("meshcore", b"\xff" * SECTOR), "companion-v1.17.0")
        self.assertEqual(installed_version("meshtastic", b"\xff" * SECTOR), "v2.7.26.54e0d8d")
        self.assertTrue(settings_erase_required("companion-v1.17.1", "companion-v1.16.0"))
        self.assertFalse(settings_erase_required("companion-v1.17.0", "companion-v1.17.1"))
        self.assertFalse(settings_erase_required("companion-v1.17.1", "companion-v1.17.1"))
        self.assertTrue(settings_erase_required(None, "companion-v1.17.1"))

    def test_firmware_boundaries_match_partition_table(self):
        import re

        root = Path(__file__).resolve().parents[1]
        by_name = {p["name"]: (p["offset"], p["size"]) for p in partitions()}
        header = (root / "include/storage_boundary.h").read_text()
        ranges = [
            (int(start, 16), int(size, 16))
            for start, size in re.findall(r"contains\((0x[0-9a-f]+),\s*(0x[0-9a-f]+)", header)
        ]
        # storage_write_allowed lists MeshCore's regions, then Meshtastic's.
        self.assertEqual(ranges, [by_name[n] for n in ("mc_nvs", "mc_fs", "mt_nvs", "mt_fs")])
        integration = (root / "scripts/firmware/integration.cpp").read_text()
        otadata = tuple(
            int(re.search(rf"{name} = (0x[0-9a-f]+);", integration).group(1), 16)
            for name in ("ota_data_offset", "ota_data_size")
        )
        self.assertEqual(otadata, by_name["otadata"])

    def test_partition_table_md5(self):
        table = partition_binary()
        self.assertEqual(len(table), 4096)
        end = len(partitions()) * 32
        self.assertEqual(table[end : end + 2], b"\xeb\xeb")
        self.assertEqual(table[end + 16 : end + 32], hashlib.md5(table[:end]).digest())


if __name__ == "__main__":
    unittest.main()
