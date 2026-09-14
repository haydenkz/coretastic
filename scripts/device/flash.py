#!/usr/bin/env python3
"""USB install, update, backup, and recovery using the release manifest."""

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path

# device/flash.py imports layout; resolve the sibling package for direct
# invocation as well as package import.
for _tooling in ["scripts/device"]:
    _tooling_path = Path(__file__).resolve().parents[2] / _tooling
    if str(_tooling_path) not in sys.path:
        sys.path.insert(0, str(_tooling_path))

from layout import BOARDS, FLASH_SIZE, load_manifest, partition_binary, sha

COMPATIBLE_BACKUP_BOARDS = {*BOARDS, "heltec-v4.2-oled", "heltec-v4.3-oled"}


def make_backup(data, mac, board):
    if len(data) != FLASH_SIZE:
        raise ValueError("Incomplete flash read")
    metadata = json.dumps(
        dict(format="coretastic-backup-v1", mac=mac, board=board, size=len(data), sha256=sha(data))
    ).encode()
    return metadata.ljust(4096, b"\0") + data


def read_backup(data, mac, board):
    if len(data) != FLASH_SIZE + 4096:
        raise ValueError("Wrong backup length")
    meta = json.loads(data[:4096].split(b"\0", 1)[0])
    flash = data[4096:]
    if (
        meta.get("format") != "coretastic-backup-v1"
        or meta.get("mac", "").lower() != mac.lower()
        or meta.get("board") not in COMPATIBLE_BACKUP_BOARDS
        or board not in COMPATIBLE_BACKUP_BOARDS
        or meta.get("size") != FLASH_SIZE
        or meta.get("sha256") != sha(flash)
    ):
        raise ValueError("Backup metadata, device identity, or checksum mismatch")
    if flash[0x8000:0x9000] != partition_binary():
        raise ValueError(
            "Backup is not a compatible dual-boot layout; use the original firmware recovery tool"
        )
    return flash


def plan(manifest, operation, read):
    if operation != "install":
        if read(0x8000, 4096) != partition_binary():
            raise ValueError("Incompatible installed layout: back up, then use install --erase")
        if operation != "recovery":
            boot = manifest["images"]["bootloader"]
            if sha(read(0, boot["size"])) != boot["sha256"]:
                raise ValueError("Bootloader differs: run recovery first")
    return (
        ["meshcore", "meshtastic", "selector", "partitions", "bootloader"]
        if operation == "install"
        else ["selector", "partitions", "bootloader"]
        if operation == "recovery"
        else [operation]
    )


def execute(args):
    import esptool

    # Validate the entire bundle before opening the device or issuing any command.
    manifest = None if args.operation in ("backup", "restore") else load_manifest(args.manifest)
    if args.operation in ("install", "restore") and not args.erase:
        raise ValueError(
            "This operation overwrites all flash. Download a backup, then supply --erase"
        )
    if args.operation in ("backup", "restore") and not args.file:
        raise ValueError("backup/restore requires --file PATH")
    backup_input = Path(args.file).read_bytes() if args.operation == "restore" else None
    esp = esptool.detect_chip(args.port, 115200)
    try:
        if esp.CHIP_NAME != "ESP32-S3":
            raise ValueError("Only ESP32-S3 is supported")
        if (
            esp.secure_download_mode
            or esp.get_secure_boot_enabled()
            or esp.get_flash_encryption_enabled()
        ):
            raise ValueError("Secure Boot / encrypted devices are unsupported")
        # The stub initializes the flash interface; ROM download mode does not.
        esp = esp.run_stub()
        if (esp.flash_id() >> 16) & 255 != 24:
            raise ValueError("Expected 16 MiB physical flash")
        mac = ":".join(f"{b:02x}" for b in esp.read_mac())
        print(
            f"Validated ESP32-S3, 16 MiB flash, MAC {mac}; physical board confirmation: {args.board}"
        )
        esp.flash_set_parameters(FLASH_SIZE)
        esp.change_baud(460800)
        if args.operation == "backup":
            # Exclusive creation prevents accidental replacement of a previous backup.
            data = make_backup(esp.read_flash(0, FLASH_SIZE), mac, args.board)
            with Path(args.file).open("xb") as output:
                output.write(data)
            print(f"Saved private full-flash backup: {args.file}")
            return
        with tempfile.TemporaryDirectory(prefix="coretastic-") as temp:
            directory = Path(temp)
            if args.operation == "restore":
                data = read_backup(backup_input, mac, args.board)
                path = directory / "restore.bin"
                path.write_bytes(data)
                files = [(0, path)]
            else:
                names = plan(manifest, args.operation, esp.read_flash)
                # Snapshot validated bytes to prevent source files changing during write.
                files = []
                for name in names:
                    image = manifest["images"][name]
                    data = (args.manifest.parent / image["file"]).read_bytes()
                    if sha(data) != image["sha256"]:
                        raise ValueError("Bundle changed after validation")
                    path = directory / image["file"]
                    path.write_bytes(data)
                    files.append((image["offset"], path))
                ota = directory / "otadata.bin"
                ota.write_bytes(b"\xff" * 8192)
                files.append((0xE000, ota))
            command = [
                "--chip",
                "esp32s3",
                "--after",
                "no_reset_stub",
                "write_flash",
                "--flash_mode",
                "keep",
                "--flash_freq",
                "keep",
                "--flash_size",
                "keep",
            ]
            if args.operation in ("install", "restore"):
                command.append("--erase-all")
            for address, file in sorted(files):
                command.extend([hex(address), str(file)])
            esptool.main(command, esp=esp)
            for address, file in files:
                data = file.read_bytes()
                if sha(esp.read_flash(address, len(data))) != sha(data):
                    raise ValueError(
                        f"Read-back verification failed at {address:#x}. Retry USB recovery"
                    )
        print("Verified. Disconnect USB and press RESET with PRG released.")
    finally:
        esp._port.close()


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)

    def flush(self):
        self.console.flush()
        self.log.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=["install", "selector", "meshcore", "meshtastic", "backup", "restore", "recovery"],
    )
    parser.add_argument("--port", required=True)
    parser.add_argument(
        "--board",
        required=True,
        choices=BOARDS,
        help="Confirm a Heltec V4.2/V4.3 OLED; R8/TFT/V3 are unsupported",
    )
    parser.add_argument("--manifest", type=Path, default=Path("release/manifest.json"))
    parser.add_argument("--file", type=Path)
    parser.add_argument(
        "--erase", action="store_true", help="Acknowledge destructive full installation/restore"
    )
    parser.add_argument("--log", type=Path, default=Path("coretastic-flash.log"))
    args = parser.parse_args()
    with args.log.open("a") as log:
        with (
            contextlib.redirect_stdout(Tee(sys.stdout, log)),
            contextlib.redirect_stderr(Tee(sys.stderr, log)),
        ):
            try:
                execute(args)
            except (Exception, KeyboardInterrupt) as error:
                print(f"FAILED: {error}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
