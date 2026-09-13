import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from flash import execute, make_backup, plan, read_backup
from layout import BOARDS, FLASH_SIZE, partition_binary
from test_layout import manifest_fixture


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
                data[0x8000:0x9000] = partition_binary()
                return data

        device = Device()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "backup.ctbackup"
            args = SimpleNamespace(operation="backup", file=file, port="test", board=BOARDS[0])
            with patch.dict(
                sys.modules, {"esptool": SimpleNamespace(detect_chip=lambda *args: device)}
            ):
                execute(args)
            self.assertEqual(
                len(read_backup(file.read_bytes(), "01:02:03:04:05:06", BOARDS[0])), FLASH_SIZE
            )
        self.assertTrue(device.started and device.closed)

    def test_plan_checks_layout_and_bootloader(self):
        m = manifest_fixture()
        with self.assertRaisesRegex(ValueError, "layout"):
            plan(m, "meshcore", lambda a, n: b"\xff" * n)
        with self.assertRaisesRegex(ValueError, "Bootloader"):
            plan(m, "meshtastic", lambda a, n: partition_binary() if a == 0x8000 else b"\xff" * n)
        self.assertEqual(
            plan(m, "recovery", lambda a, n: partition_binary()),
            ["selector", "partitions", "bootloader"],
        )
        self.assertEqual(
            len(
                plan(
                    m, "install", lambda a, n: self.fail("Install must not require existing layout")
                )
            ),
            5,
        )

    def test_backup_identity_and_integrity(self):
        flash = bytearray(b"\xff" * FLASH_SIZE)
        flash[0x8000:0x9000] = partition_binary()
        saved = make_backup(flash, "aa:bb:cc:dd:ee:ff", BOARDS[0])
        self.assertEqual(read_backup(saved, "aa:bb:cc:dd:ee:ff", BOARDS[0]), flash)
        with self.assertRaises(ValueError):
            read_backup(saved, "00:00:00:00:00:00", BOARDS[0])
        with self.assertRaises(ValueError):
            read_backup(saved[:-1], "aa:bb:cc:dd:ee:ff", BOARDS[0])
        broken = bytearray(saved)
        broken[4097] ^= 1
        with self.assertRaises(ValueError):
            read_backup(broken, "aa:bb:cc:dd:ee:ff", BOARDS[0])
