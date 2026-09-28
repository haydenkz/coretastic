"""Flash contract shared by packaging, validation, and the USB CLI."""

import csv
import functools
import hashlib
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECTOR = 0x1000
MANIFEST_SCHEMA = 3
APPS = ("meshcore", "meshtastic")
# Each app's private settings; a downgrade erases them because older firmware
# may not read what a newer version wrote.
SETTINGS = {"meshcore": ("mc_nvs", "mc_fs"), "meshtastic": ("mt_nvs", "mt_fs")}
# The flasher records the installed version in the last sector of each app
# partition. The bootloader ignores bytes past the image and the apps cannot
# write there. A board's legacy_versions name what a flash without a record runs
# (Coretastic v0.1.0 wrote none and supported only the Heltec V4).
RECORD_FORMAT = "coretastic-app-v1"
STORAGE_EPOCH = {"meshcore": 1, "meshtastic": 1, "selector": 1}
# Every layout reserves the same boot metadata region.
OTADATA = (0xE000, 0x2000)


@functools.cache
def boards():
    """Board profiles from boards/<id>/board.json, keyed by id."""
    result = {}
    for path in sorted((ROOT / "boards").glob("*/board.json")):
        profile = json.loads(path.read_text())
        size = profile.get("flash_size")
        if size not in (0x400000, 0x800000, 0x1000000):
            raise ValueError(f"{path}: unsupported flash size")
        if set(profile.get("environments", {})) != set(APPS):
            raise ValueError(f"{path}: needs an upstream environment for each app")
        result[path.parent.name] = dict(profile, id=path.parent.name)
    # Supported boards first; experimental ones last.
    return dict(sorted(result.items(), key=lambda item: (item[1]["experimental"], item[0])))


def board(board_id):
    try:
        return boards()[board_id]
    except KeyError:
        raise ValueError(f"Unknown board {board_id!r}; known: {', '.join(boards())}") from None


def flash_size(board_id):
    return board(board_id)["flash_size"]


@functools.cache
def partitions(board_id):
    path = ROOT / "boards" / board_id / "partitions.csv"
    size_limit = flash_size(board_id)
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
        if previous > size_limit:
            raise ValueError(f"{board_id}: partition {part['name']} outside flash")
        names.add(part["name"])
    required = {"selector_nvs", "otadata", "selector", *APPS}
    required |= {name for names in SETTINGS.values() for name in names}
    if names != required:
        raise ValueError(f"{board_id}: partitions must be exactly {sorted(required)}")
    otadata = next(p for p in result if p["name"] == "otadata")
    if (otadata["offset"], otadata["size"]) != OTADATA:
        raise ValueError(f"{board_id}: otadata must be at {OTADATA[0]:#x}")
    # Returned lists are shared by the cache; hand out copies.
    return [dict(part) for part in result]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def partition_binary(board_id):
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
        for p in partitions(board_id)
    )
    table += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(table).digest()
    return table.ljust(SECTOR, b"\xff")


def flash_size_code(size):
    """The image header's flash size field: 2 = 4 MiB, 3 = 8 MiB, 4 = 16 MiB."""
    return (size >> 20).bit_length() - 1


def validate_image(data, app=True, size=0x1000000):
    if len(data) < 48 or data[0] != 0xE9 or not 1 <= data[1] <= 16:
        raise ValueError("Invalid ESP image header")
    if struct.unpack_from("<H", data, 12)[0] != 9 or data[3] >> 4 != flash_size_code(size):
        raise ValueError(f"Image must target ESP32-S3 with {size >> 20} MiB flash")
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


def partition(board_id, name):
    return next(p for p in partitions(board_id) if p["name"] == name)


def record_offset(board_id, component):
    part = partition(board_id, component)
    return part["offset"] + part["size"] - SECTOR


def layout_header(board_id):
    """C++ constants for the apps' flash write guard, generated from the partition table."""
    parts = {p["name"]: p for p in partitions(board_id)}
    lines = [
        "#pragma once",
        f"// Generated from boards/{board_id}/partitions.csv; do not edit.",
        "#include <cstdint>",
        "namespace coretastic {",
        f"constexpr uint32_t kFlashSize = {flash_size(board_id):#x};",
    ]
    for constant, name in [
        ("OtaData", "otadata"),
        ("McNvs", "mc_nvs"),
        ("McFs", "mc_fs"),
        ("MtNvs", "mt_nvs"),
        ("MtFs", "mt_fs"),
    ]:
        lines.append(f"constexpr uint32_t k{constant}Offset = {parts[name]['offset']:#x};")
        lines.append(f"constexpr uint32_t k{constant}Size = {parts[name]['size']:#x};")
    lines.append("} // namespace coretastic")
    return "\n".join(lines) + "\n"


def version_key(version):
    """Orders upstream tags such as companion-v1.17.1 and v2.7.26.54e0d8d."""
    match = isinstance(version, str) and re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(part) for part in match.groups()) if match else None


def app_record(component, version, digest):
    record = dict(format=RECORD_FORMAT, component=component, version=version, sha256=digest)
    data = json.dumps(record, separators=(",", ":")).encode() + b"\0"
    return data.ljust(SECTOR, b"\xff")


def installed_version(board_id, component, record):
    """Returns the installed version from its record, or None if it is unreadable."""
    if record[:1] == b"\xff":
        return board(board_id)["legacy_versions"].get(component)
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


def board_file(board_id, name):
    return f"{board_id}-{name}.bin"


def check_image(label, image, filename, offset, limit, directory, kind, board_id):
    if not isinstance(image, dict):
        raise ValueError(f"{label}: missing image")
    if image.get("file") != filename or image.get("offset") != offset:
        raise ValueError(f"{label}: wrong filename or offset")
    size = image.get("size")
    if type(size) is not int or not 0 < size <= limit or (size + 4095) // 4096 * 4096 > limit:
        raise ValueError(f"{label}: image exceeds its write boundary")
    digest = image.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}: invalid checksum")
    if kind == "partitions" and (size != SECTOR or digest != sha(partition_binary(board_id))):
        raise ValueError(f"{label}: partition binary differs from layout")
    if directory:
        data = (Path(directory) / filename).read_bytes()
        if len(data) != size or sha(data) != digest:
            raise ValueError(f"{label}: size/checksum mismatch")
        if kind != "partitions":
            validate_image(data, kind == "app", flash_size(board_id))


def validate_board(board_id, release, directory=None):
    profile = board(board_id)
    if not isinstance(release, dict):
        raise ValueError(f"{board_id}: invalid board entry")
    if release.get("layout") != profile["layout"] or release.get("flash_size") != flash_size(
        board_id
    ):
        raise ValueError(f"{board_id}: layout or flash size differs from this flasher")
    if release.get("partitions") != partitions(board_id):
        raise ValueError(f"{board_id}: partition map differs from this flasher")
    images = release.get("images", {})
    selector = partition(board_id, "selector")
    expected = {
        "selector": (selector["offset"], selector["size"], "app"),
        "bootloader": (0, 0x8000, "bootloader"),
        "partitions": (0x8000, SECTOR, "partitions"),
    }
    if not isinstance(images, dict) or images.keys() != expected.keys():
        raise ValueError(f"{board_id}: missing or unexpected image")
    for name, (offset, limit, kind) in expected.items():
        filename = board_file(board_id, name)
        label = f"{board_id} {name}"
        check_image(label, images[name], filename, offset, limit, directory, kind, board_id)
    apps = release.get("apps")
    if not isinstance(apps, dict) or apps.keys() != set(APPS):
        raise ValueError(f"{board_id}: missing or unexpected app")
    for component in APPS:
        entries = apps[component]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{board_id} {component}: no versions")
        part = partition(board_id, component)
        keys = []
        for entry in entries:
            version = entry.get("version") if isinstance(entry, dict) else None
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
                raise ValueError(f"{board_id} {component}: invalid version")
            keys.append(version_key(version))
            if keys[-1] is None:
                raise ValueError(f"{board_id} {component}: unorderable version {version}")
            commit = entry.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError(f"{board_id} {component} {version}: invalid commit")
            # The last sector of the partition holds the version record.
            limit = part["size"] - SECTOR
            label = f"{board_id} {component} {version}"
            filename = board_file(board_id, f"{component}-{version}")
            check_image(label, entry, filename, part["offset"], limit, directory, "app", board_id)
        if any(newer <= older for newer, older in zip(keys, keys[1:])):
            raise ValueError(f"{board_id} {component}: versions must be unique and newest first")


def validate_manifest(manifest, directory=None):
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("Unsupported manifest schema; use the flasher from the same release")
    if manifest.get("chip") != "ESP32-S3":
        raise ValueError("Unsupported hardware")
    if manifest.get("storage_epoch") != STORAGE_EPOCH:
        raise ValueError("Incompatible storage format")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ValueError("Missing release version")
    releases = manifest.get("boards")
    if not isinstance(releases, dict) or not releases:
        raise ValueError("Release lists no boards")
    for board_id, release in releases.items():
        board(board_id)  # Rejects boards this flasher does not know.
        validate_board(board_id, release, directory)
    return manifest


def load_manifest(path):
    path = Path(path)
    return validate_manifest(json.loads(path.read_text()), path.parent)
