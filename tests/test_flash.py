import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "device"))
from flash import execute, make_backup, plan, read_backup
from layout import (
    SECTOR,
    app_record,
    flash_size,
    installed_version,
    partition,
    partition_binary,
    record_offset,
    sha,
)
from test_layout import V3, V4, image_fixture, manifest_fixture

FLASH_SIZE = flash_size(V4)


class FlashTests(unittest.TestCase):
    def test_backup_initializes_stub_before_flash_access(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        class Device:
            CHIP_NAME = "ESP32-S3"
            secure_download_mode = False
            started = False
            closed = False

            def __init__(self):
                self._port = SimpleNamespace(close=lambda: setattr(self, "closed", True))

            def get_secure_boot_enabled(self):
                return False

            def get_flash_encryption_enabled(self):
                return False

            def run_stub(self):
                self.started = True
                return self

            def flash_id(self):
                assert self.started
                return 0x1840EF

            def flash_set_parameters(self, size):
                assert size == FLASH_SIZE

            def change_baud(self, baud):
                assert baud == 460800

            def read_mac(self):
                return bytes([1, 2, 3, 4, 5, 6])

            def read_flash(self, address, size):
                assert self.started and address == 0 and size == FLASH_SIZE
                data = bytearray(b"\xff" * size)
                data[0x8000:0x9000] = partition_binary(V4)
                return data

        device = Device()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "backup.ctbackup"
            args = SimpleNamespace(operation="backup", file=file, port="test", board=V4)
            with patch.dict(
                sys.modules, {"esptool": SimpleNamespace(detect_chip=lambda *args: device)}
            ):
                execute(args)
            self.assertEqual(
                len(read_backup(file.read_bytes(), "01:02:03:04:05:06", V4)), FLASH_SIZE
            )
        self.assertTrue(device.started and device.closed)

    def test_install_reuses_initialized_stub(self):
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        class Device:
            CHIP_NAME = "ESP32-S3"
            secure_download_mode = False

            def __init__(self):
                self._port = SimpleNamespace(close=lambda: None)
                self.sync_stub_detected = False
                self.written = {}

            def get_secure_boot_enabled(self):
                return False

            def get_flash_encryption_enabled(self):
                return False

            def run_stub(self):
                return self

            def flash_id(self):
                return 0x1840EF

            def flash_set_parameters(self, size):
                self.assert_size = size

            def change_baud(self, baud):
                self.assert_baud = baud

            def read_mac(self):
                return bytes([1, 2, 3, 4, 5, 6])

            def read_flash(self, address, size):
                return self.written[address][:size]

            def flash_md5sum(self, address, size):
                return hashlib.md5(self.written[address][:size]).hexdigest()

        device = Device()

        def write_flash(command, esp):
            self.assertIs(esp, device)
            self.assertTrue(esp.sync_stub_detected)
            self.assertEqual(command[0:4], ["--port", "test", "--chip", "esp32s3"])
            for index, value in enumerate(command[:-1]):
                if value.startswith("0x"):
                    esp.written[int(value, 16)] = Path(command[index + 1]).read_bytes()

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            manifest = manifest_fixture()
            del manifest["boards"][V3]  # This bundle ships only V4 images.
            release = manifest["boards"][V4]
            images = list(release["images"].items())
            images += [(c, e) for c, entries in release["apps"].items() for e in entries]
            for name, image in images:
                data = partition_binary(V4) if name == "partitions" else image_fixture()
                (directory / image["file"]).write_bytes(data)
                image["size"] = len(data)
                image["sha256"] = sha(data)
            manifest_path = directory / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            args = SimpleNamespace(
                operation="install",
                file=None,
                port="test",
                board=V4,
                manifest=manifest_path,
                erase=True,
                meshcore_version="companion-v1.16.0",
                meshtastic_version=None,
            )
            with patch.dict(
                sys.modules,
                {
                    "esptool": SimpleNamespace(
                        detect_chip=lambda *args: device,
                        main=write_flash,
                    )
                },
            ):
                execute(args)

        self.assertEqual(device.assert_size, FLASH_SIZE)
        self.assertEqual(device.assert_baud, 460800)
        self.assertIn(0xE000, device.written)
        self.assertEqual(
            installed_version(V4, "meshcore", device.written[record_offset(V4, "meshcore")]),
            "companion-v1.16.0",
        )
        self.assertEqual(
            installed_version(V4, "meshtastic", device.written[record_offset(V4, "meshtastic")]),
            "v2.7.26.54e0d8d",
        )

    def test_backup_refuses_existing_file_before_connecting(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "backup.ctbackup"
            file.write_bytes(b"previous")
            args = SimpleNamespace(operation="backup", file=file, port="test", board=V4)
            esptool = SimpleNamespace(detect_chip=lambda *args: self.fail("Device opened"))
            with patch.dict(sys.modules, {"esptool": esptool}):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    execute(args)
            self.assertEqual(file.read_bytes(), b"previous")

    def test_plan_checks_layout_and_bootloader(self):
        m = manifest_fixture()
        with self.assertRaisesRegex(ValueError, "layout"):
            plan(m, V4, "meshcore", lambda a, n: b"\xff" * n)
        with self.assertRaisesRegex(ValueError, "Bootloader"):
            plan(
                m,
                V4,
                "meshtastic",
                lambda a, n: partition_binary(V4) if a == 0x8000 else b"\xff" * n,
            )
        self.assertEqual(
            [offset for offset, _ in plan(m, V4, "recovery", lambda a, n: partition_binary(V4))],
            [0x10000, 0x8000, 0, 0xE000],
        )
        writes = plan(m, V4, "install", lambda a, n: self.fail("Install must not require a layout"))
        self.assertEqual(
            [offset for offset, _ in writes],
            [0x100000, 0x3FF000, 0x400000, 0x9FF000, 0x10000, 0x8000, 0, 0xE000],
        )
        self.assertEqual(writes[0][1]["version"], "companion-v1.17.1", "defaults to newest")

    def test_plan_selects_versions_and_guards_downgrades(self):
        m = manifest_fixture()

        def device(installed):
            def read(address, size):
                if address == 0x8000:
                    return partition_binary(V4)
                if address == 0:
                    return b"\xff" * size
                if address == record_offset(V4, "meshcore"):
                    return installed
                raise AssertionError(f"Unexpected read at {address:#x}")

            return read

        m["boards"][V4]["images"]["bootloader"]["sha256"] = sha(b"\xff" * 336)
        newest = device(app_record("meshcore", "companion-v1.17.1", "a" * 64))
        with self.assertRaisesRegex(ValueError, "not in this release.*companion-v1.17.1"):
            plan(m, V4, "meshcore", newest, dict(meshcore="companion-v9.9.9"))
        with self.assertRaisesRegex(ValueError, "older than the installed companion-v1.17.1"):
            plan(m, V4, "meshcore", newest, dict(meshcore="companion-v1.16.0"))
        downgrade = plan(m, V4, "meshcore", newest, dict(meshcore="companion-v1.16.0"), True)
        erased = {offset: data for offset, data in downgrade if isinstance(data, bytes)}
        for name in ("mc_nvs", "mc_fs"):
            self.assertEqual(
                erased[partition(V4, name)["offset"]], b"\xff" * partition(V4, name)["size"]
            )
        self.assertNotIn(partition(V4, "mt_nvs")["offset"], erased)
        self.assertEqual(
            installed_version(V4, "meshcore", erased[record_offset(V4, "meshcore")]),
            "companion-v1.16.0",
        )
        # Upgrading keeps settings; an unreadable record is treated as a downgrade.
        legacy = device(b"\xff" * SECTOR)
        upgrade = plan(m, V4, "meshcore", legacy, dict(meshcore="companion-v1.17.1"))
        self.assertEqual(
            [offset for offset, _ in upgrade], [0x100000, record_offset(V4, "meshcore"), 0xE000]
        )
        with self.assertRaisesRegex(ValueError, "unidentified version"):
            plan(m, V4, "meshcore", device(b"garbage".ljust(SECTOR, b"\0")))

    def test_backup_identity_and_integrity(self):
        flash = bytearray(b"\xff" * FLASH_SIZE)
        flash[0x8000:0x9000] = partition_binary(V4)
        saved = make_backup(flash, "aa:bb:cc:dd:ee:ff", V4)
        self.assertEqual(read_backup(saved, "aa:bb:cc:dd:ee:ff", V4), flash)
        # Earlier flashers recorded the V4 revision instead of the board id.
        meta = json.loads(saved[:4096].rstrip(b"\0"))
        meta["board"] = "heltec-v4.2-oled"
        legacy = json.dumps(meta).encode().ljust(4096, b"\0") + saved[4096:]
        self.assertEqual(read_backup(legacy, "aa:bb:cc:dd:ee:ff", V4), flash)
        with self.assertRaises(ValueError):
            read_backup(saved, "00:00:00:00:00:00", V4)
        with self.assertRaises(ValueError):
            read_backup(saved[:-1], "aa:bb:cc:dd:ee:ff", V4)
        broken = bytearray(saved)
        broken[4097] ^= 1
        with self.assertRaises(ValueError):
            read_backup(broken, "aa:bb:cc:dd:ee:ff", V4)
        for header in (b"not json", b"[]", b'{"format": "coretastic-backup-v1", "mac": 1}'):
            with self.assertRaisesRegex(ValueError, "metadata"):
                read_backup(header.ljust(4096, b"\0") + flash, "aa:bb:cc:dd:ee:ff", V4)

    def test_experimental_board_needs_opt_in_and_uses_its_layout(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(
                operation="backup", file=Path(temp) / "v3.ctbackup", port="test", board=V3
            )
            esptool = SimpleNamespace(detect_chip=lambda *args: self.fail("Device opened"))
            with patch.dict(sys.modules, {"esptool": esptool}):
                with self.assertRaisesRegex(ValueError, "--experimental"):
                    execute(args)
        m = manifest_fixture()
        writes = plan(m, V3, "install", lambda a, n: self.fail("Install must not read"))
        self.assertEqual(
            [offset for offset, _ in writes],
            [0x60000, 0x25F000, 0x260000, 0x55F000, 0x10000, 0x8000, 0, 0xE000],
        )
        self.assertEqual(writes[0][1]["file"], "heltec-v3-meshcore-companion-v1.17.1.bin")
        # A V3 without a version record cannot be identified, so updates erase settings.
        m["boards"][V3]["images"]["bootloader"]["sha256"] = sha(b"\xff" * 336)

        def blank_v3(address, size):
            return partition_binary(V3) if address == 0x8000 else b"\xff" * size

        with self.assertRaisesRegex(ValueError, "unidentified version"):
            plan(m, V3, "meshtastic", blank_v3)
        # The V4's table does not pass as a V3 layout.
        with self.assertRaisesRegex(ValueError, "layout"):
            plan(m, V3, "meshcore", lambda a, n: partition_binary(V4))
        del m["boards"][V3]
        with self.assertRaisesRegex(ValueError, "no images for heltec-v3"):
            plan(m, V3, "install", blank_v3)

    def test_backups_are_bound_to_their_board(self):
        v3_flash = bytearray(b"\xff" * flash_size(V3))
        v3_flash[0x8000:0x9000] = partition_binary(V3)
        saved = make_backup(v3_flash, "aa:bb:cc:dd:ee:ff", V3)
        self.assertEqual(read_backup(saved, "aa:bb:cc:dd:ee:ff", V3), v3_flash)
        with self.assertRaisesRegex(ValueError, "length"):
            read_backup(saved, "aa:bb:cc:dd:ee:ff", V4)
        v4_flash = bytearray(b"\xff" * FLASH_SIZE)
        v4_flash[0x8000:0x9000] = partition_binary(V4)
        with self.assertRaisesRegex(ValueError, "length"):
            read_backup(make_backup(v4_flash, "aa:bb:cc:dd:ee:ff", V4), "aa:bb:cc:dd:ee:ff", V3)
        # V4.2/V4.3 backups from before board ids restore on the V4 only.
        with self.assertRaises(ValueError):
            make_backup(v4_flash, "aa:bb:cc:dd:ee:ff", V3)
