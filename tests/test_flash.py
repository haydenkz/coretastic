import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from flash import make_backup, plan, read_backup
from layout import BOARDS, FLASH_SIZE, partition_binary
from test_layout import manifest_fixture


class FlashTests(unittest.TestCase):
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
