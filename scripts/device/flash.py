#!/usr/bin/env python3
"""USB install, update, backup, and recovery using the release manifest."""

import argparse
import contextlib
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from layout import (
    APPS,
    BOARDS,
    FLASH_SIZE,
    SECTOR,
    SETTINGS,
    app_record,
    installed_version,
    load_manifest,
    partition,
    partition_binary,
    record_offset,
    settings_erase_required,
    sha,
)

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
    try:
        meta = json.loads(data[:4096].split(b"\0", 1)[0])
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        raise ValueError("Invalid backup metadata")
    flash = data[4096:]
    if (
        meta.get("format") != "coretastic-backup-v1"
        or not isinstance(meta.get("mac"), str)
        or meta["mac"].lower() != mac.lower()
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


def select_app(manifest, component, version=None):
    """Returns the manifest entry for a version, defaulting to the newest."""
    entries = manifest["apps"][component]
    if version is None:
        return entries[0]
    for entry in entries:
        if entry["version"] == version:
            return entry
    available = ", ".join(entry["version"] for entry in entries)
    raise ValueError(f"{component} {version} is not in this release; available: {available}")


def plan(manifest, operation, read, versions=None, erase_settings=False):
    """Returns (offset, source) writes; a source is a manifest image or raw bytes."""
    versions = versions or {}
    if operation != "install":
        if read(0x8000, 4096) != partition_binary():
            raise ValueError("Incompatible installed layout: back up, then use install --erase")
        if operation != "recovery":
            boot = manifest["images"]["bootloader"]
            if sha(read(0, boot["size"])) != boot["sha256"]:
                raise ValueError("Bootloader differs: run recovery first")
    writes = []
    for component in APPS if operation == "install" else [operation] if operation in APPS else []:
        entry = select_app(manifest, component, versions.get(component))
        writes.append((entry["offset"], entry))
        record = app_record(component, entry["version"], entry["sha256"])
        writes.append((record_offset(component), record))
        if operation == "install":
            continue  # The whole chip is erased.
        installed = installed_version(component, read(record_offset(component), SECTOR))
        required = settings_erase_required(installed, entry["version"])
        if required and not erase_settings:
            raise ValueError(
                f"{component}: {entry['version']} is older than the installed "
                f"{installed or 'unidentified version'}, so its settings must be erased. "
                "Back up, then rerun with --erase-settings"
            )
        if erase_settings:
            for name in SETTINGS[component]:
                part = partition(name)
                writes.append((part["offset"], b"\xff" * part["size"]))
    shared = {"install": ["selector", "partitions", "bootloader"], "selector": ["selector"]}
    shared["recovery"] = shared["install"]
    for name in shared.get(operation, []):
        writes.append((manifest["images"][name]["offset"], manifest["images"][name]))
    # Erased OTA metadata returns to the factory selector after a USB operation.
    writes.append((0xE000, b"\xff" * 8192))
    return writes


def execute(args):
    import esptool

    # Validate the entire bundle before opening the device or issuing any command.
    manifest = None if args.operation in ("backup", "restore") else load_manifest(args.manifest)
    versions = {component: getattr(args, f"{component}_version", None) for component in APPS}
    if manifest:
        for component, version in versions.items():
            select_app(manifest, component, version)
    if args.operation in ("install", "restore") and not args.erase:
        raise ValueError(
            "This operation overwrites all flash. Download a backup, then supply --erase"
        )
    if args.operation in ("backup", "restore") and not args.file:
        raise ValueError("backup/restore requires --file PATH")
    # Refuse before the multi-minute flash read rather than after it.
    if args.operation == "backup" and Path(args.file).exists():
        raise ValueError(f"{args.file} already exists; choose a new backup file")
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
                erase_settings = getattr(args, "erase_settings", False)
                writes = plan(manifest, args.operation, esp.read_flash, versions, erase_settings)
                # Snapshot validated bytes to prevent source files changing during write.
                files = []
                for offset, source in writes:
                    if isinstance(source, dict):
                        data = (args.manifest.parent / source["file"]).read_bytes()
                        if sha(data) != source["sha256"]:
                            raise ValueError("Bundle changed after validation")
                        print(f"Writing {source['file']} at {offset:#x}")
                    else:
                        data = source
                    path = directory / f"{offset:08x}.bin"
                    path.write_bytes(data)
                    files.append((offset, path))
            command = [
                "--port",
                args.port,
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
            # The stub is already running on `esp`; tell esptool.main not to
            # upload it a second time (overlapping RAM raises FatalError).
            esp.sync_stub_detected = True
            esptool.main(command, esp=esp)
            # The stub hashes each range on the device, so nothing is read back over USB.
            for address, file in files:
                data = file.read_bytes()
                if esp.flash_md5sum(address, len(data)) != hashlib.md5(data).hexdigest():
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
        "--meshcore-version", help="MeshCore version to install (default: newest in the release)"
    )
    parser.add_argument(
        "--meshtastic-version",
        help="Meshtastic version to install (default: newest in the release)",
    )
    parser.add_argument(
        "--erase-settings",
        action="store_true",
        help="Erase the updated app's settings; required when installing an older version",
    )
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
