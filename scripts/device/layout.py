"""Flash contract shared by packaging, validation, and the USB CLI."""

import csv
import hashlib
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASH_SIZE = 0x1000000
SECTOR = 0x1000
LAYOUT_ID = "heltec-v4-dual-v1"
MANIFEST_SCHEMA = 2
BOARD = "heltec-v4-oled"
BOARDS = [BOARD]
APPS = ("meshcore", "meshtastic")
# Each app's private settings; a downgrade erases them because older firmware
# may not read what a newer version wrote.
SETTINGS = {"meshcore": ("mc_nvs", "mc_fs"), "meshtastic": ("mt_nvs", "mt_fs")}
# The flasher records the installed version in the last sector of each app
# partition. The bootloader ignores bytes past the image and the apps cannot
# write there. Coretastic v0.1.0 wrote no record and shipped exactly these.
RECORD_FORMAT = "coretastic-app-v1"
LEGACY_VERSIONS = {"meshcore": "companion-v1.17.0", "meshtastic": "v2.7.26.54e0d8d"}
STORAGE_EPOCH = {"meshcore": 1, "meshtastic": 1, "selector": 1}


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


def partition(name):
    return next(p for p in partitions() if p["name"] == name)


def record_offset(component):
    part = partition(component)
    return part["offset"] + part["size"] - SECTOR


def version_key(version):
    """Orders upstream tags such as companion-v1.17.1 and v2.7.26.54e0d8d."""
    match = isinstance(version, str) and re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(part) for part in match.groups()) if match else None


def app_record(component, version, digest):
    record = dict(format=RECORD_FORMAT, component=component, version=version, sha256=digest)
    data = json.dumps(record, separators=(",", ":")).encode() + b"\0"
    return data.ljust(SECTOR, b"\xff")


def installed_version(component, record):
    """Returns the installed version from its record, or None if it is unreadable."""
    if record[:1] == b"\xff":
        return LEGACY_VERSIONS[component]
    try:
        meta = json.loads(bytes(record).split(b"\0", 1)[0])
    except ValueError:
        return None
    if (
        not isinstance(meta, dict)
        or meta.get("format") != RECORD_FORMAT
        or meta.get("component") != component
        or version_key(meta.get("version")) is None
    ):
        return None
    return meta["version"]


def settings_erase_required(installed, target):
    """Moving to an older (or unidentifiable) version must erase that app's settings."""
    installed_key, target_key = version_key(installed), version_key(target)
    return installed_key is None or target_key is None or target_key < installed_key


def check_image(label, image, filename, offset, limit, directory, kind):
    if image.get("file") != filename or image.get("offset") != offset:
        raise ValueError(f"{label}: wrong filename or offset")
    size = image.get("size")
    if type(size) is not int or not 0 < size <= limit or (size + 4095) // 4096 * 4096 > limit:
        raise ValueError(f"{label}: image exceeds its write boundary")
    digest = image.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}: invalid checksum")
    if kind == "partitions" and (size != SECTOR or digest != sha(partition_binary())):
        raise ValueError("Partition binary differs from layout")
    if directory:
        data = (Path(directory) / filename).read_bytes()
        if len(data) != size or sha(data) != digest:
            raise ValueError(f"{label}: size/checksum mismatch")
        if kind != "partitions":
            validate_image(data, kind == "app")


def validate_manifest(manifest, directory=None):
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("layout") != LAYOUT_ID:
        raise ValueError("Unsupported manifest schema or layout")
    if manifest.get("chip") != "ESP32-S3" or manifest.get("flash_size") != FLASH_SIZE:
        raise ValueError("Unsupported hardware")
    if manifest.get("boards") != BOARDS or manifest.get("partitions") != partitions():
        raise ValueError("Partition/hardware contract differs from this flasher")
    if manifest.get("storage_epoch") != STORAGE_EPOCH:
        raise ValueError("Incompatible storage format")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ValueError("Missing release version")
    images = manifest.get("images", {})
    selector = partition("selector")
    expected = {
        "selector": (selector["offset"], selector["size"], "app"),
        "bootloader": (0, 0x8000, "bootloader"),
        "partitions": (0x8000, SECTOR, "partitions"),
    }
    if images.keys() != expected.keys():
        raise ValueError("Missing or unexpected image")
    for name, (offset, limit, kind) in expected.items():
        check_image(name, images[name], f"{name}.bin", offset, limit, directory, kind)
    apps = manifest.get("apps")
    if not isinstance(apps, dict) or apps.keys() != set(APPS):
        raise ValueError("Missing or unexpected app")
    for component in APPS:
        entries = apps[component]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{component}: no versions")
        part = partition(component)
        keys = []
        for entry in entries:
            version = entry.get("version") if isinstance(entry, dict) else None
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
                raise ValueError(f"{component}: invalid version")
            keys.append(version_key(version))
            if keys[-1] is None:
                raise ValueError(f"{component}: unorderable version {version}")
            commit = entry.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError(f"{component} {version}: invalid commit")
            # The last sector of the partition holds the version record.
            limit = part["size"] - SECTOR
            label = f"{component} {version}"
            filename = f"{component}-{version}.bin"
            check_image(label, entry, filename, part["offset"], limit, directory, "app")
        if any(newer <= older for newer, older in zip(keys, keys[1:])):
            raise ValueError(f"{component}: versions must be unique and newest first")
    return manifest


def load_manifest(path):
    path = Path(path)
    return validate_manifest(json.loads(path.read_text()), path.parent)
