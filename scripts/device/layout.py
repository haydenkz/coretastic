"""Flash contract shared by packaging, validation, and the USB CLI."""

import csv
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASH_SIZE = 0x1000000
SECTOR = 0x1000
LAYOUT_ID = "heltec-v4-dual-v1"
BOARD = "heltec-v4-oled"
BOARDS = [BOARD]


def partitions(path=ROOT / "partitions.csv"):
    result = []
    for row in csv.reader(
        line for line in path.read_text().splitlines() if not line.startswith("#")
    ):
        if not row:
            continue
        name, kind, subtype, offset, size, flags = [field.strip() for field in row]
        if flags:
            raise ValueError("Encrypted/flagged partitions are unsupported")
        result.append(
            dict(name=name, type=kind, subtype=subtype, offset=int(offset, 0), size=int(size, 0))
        )
    previous = 0x9000
    names = set()
    for part in result:
        if part["name"] in names or part["offset"] < previous or part["size"] <= 0:
            raise ValueError("Duplicate, overlapping, or empty partition")
        alignment = 0x10000 if part["type"] == "app" else SECTOR
        if part["offset"] % alignment or part["size"] % SECTOR:
            raise ValueError("Unaligned partition")
        previous = part["offset"] + part["size"]
        if previous > FLASH_SIZE:
            raise ValueError("Partition outside 16 MiB flash")
        names.add(part["name"])
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def partition_binary():
    types = {"app": 0, "data": 1}
    subtypes = {"factory": 0, "ota_0": 16, "ota_1": 17, "nvs": 2, "ota": 0, "spiffs": 130}
    table = b"".join(
        struct.pack(
            "<HBBII16sI",
            0x50AA,
            types[p["type"]],
            subtypes[p["subtype"]],
            p["offset"],
            p["size"],
            p["name"].encode(),
            0,
        )
        for p in partitions()
    )
    table += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(table).digest()
    return table.ljust(SECTOR, b"\xff")


def validate_image(data, app=True):
    if len(data) < 48 or data[0] != 0xE9 or not 1 <= data[1] <= 16:
        raise ValueError("Invalid ESP image header")
    if struct.unpack_from("<H", data, 12)[0] != 9 or data[3] >> 4 != 4:
        raise ValueError("Image must target ESP32-S3 with 16 MiB flash")
    if data[23] != 1:
        raise ValueError("Image must include an appended SHA-256")
    pos, checksum = 24, 0xEF
    for _ in range(data[1]):
        if pos + 8 > len(data):
            raise ValueError("Truncated segment header")
        size = struct.unpack_from("<I", data, pos + 4)[0]
        pos += 8
        if size > len(data) - pos:
            raise ValueError("Truncated segment")
        for byte in data[pos : pos + size]:
            checksum ^= byte
        pos += size
    checksum_pos = (pos // 16 + 1) * 16 - 1
    if checksum_pos + 33 != len(data) or data[checksum_pos] != checksum:
        raise ValueError("Image checksum or length mismatch")
    if hashlib.sha256(data[: checksum_pos + 1]).digest() != data[checksum_pos + 1 :]:
        raise ValueError("Image embedded SHA-256 mismatch")
    if app and struct.unpack_from("<I", data, 32)[0] != 0xABCD5432:
        raise ValueError("Missing application descriptor")


def validate_manifest(manifest, directory=None):
    if manifest.get("schema") != 1 or manifest.get("layout") != LAYOUT_ID:
        raise ValueError("Unsupported manifest schema or layout")
    if manifest.get("chip") != "ESP32-S3" or manifest.get("flash_size") != FLASH_SIZE:
        raise ValueError("Unsupported hardware")
    if manifest.get("boards") != BOARDS or manifest.get("partitions") != partitions():
        raise ValueError("Partition/hardware contract differs from this flasher")
    if manifest.get("storage_epoch") != {"meshcore": 1, "meshtastic": 1, "selector": 1}:
        raise ValueError("Incompatible storage format")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ValueError("Missing release version")
    images = manifest.get("images", {})
    expected = {p["name"]: (p["offset"], p["size"]) for p in partitions() if p["type"] == "app"}
    expected.update(bootloader=(0, 0x8000), partitions=(0x8000, SECTOR))
    if images.keys() != expected.keys():
        raise ValueError("Missing or unexpected image")
    for name, (offset, limit) in expected.items():
        image = images[name]
        if image.get("file") != f"{name}.bin" or image.get("offset") != offset:
            raise ValueError(f"{name}: wrong filename or offset")
        size = image.get("size")
        if type(size) is not int or not 0 < size <= limit or (size + 4095) // 4096 * 4096 > limit:
            raise ValueError(f"{name}: image exceeds its write boundary")
        digest = image.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"{name}: invalid checksum")
        if name == "partitions" and (size != SECTOR or digest != sha(partition_binary())):
            raise ValueError("Partition binary differs from layout")
        if directory:
            data = (Path(directory) / image["file"]).read_bytes()
            if len(data) != size or sha(data) != digest:
                raise ValueError(f"{name}: size/checksum mismatch")
            if name != "partitions":
                validate_image(data, name != "bootloader")
    return manifest


def load_manifest(path):
    path = Path(path)
    return validate_manifest(json.loads(path.read_text()), path.parent)
