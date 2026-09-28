import csv from "../../partitions.csv?raw";
import { sha256 } from "@noble/hashes/sha2.js";
import { md5 } from "@noble/hashes/legacy.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const FLASH_SIZE = 0x1000000;
export const SECTOR = 4096;
export const LAYOUT = "heltec-v4-dual-v1";
export const MANIFEST_SCHEMA = 2;
export const BOARD = "heltec-v4-oled";
export const BOARDS = [BOARD];
export const partitions = csv
  .split("\n")
  .filter((x) => x && !x.startsWith("#"))
  .map((line) => {
    const [name, type, subtype, offset, size] = line
      .split(",")
      .map((x) => x.trim());
    return { name, type, subtype, offset: Number(offset), size: Number(size) };
  });
export const hash = (bytes: Uint8Array) => bytesToHex(sha256(bytes));
export const md5hex = (bytes: Uint8Array) => bytesToHex(md5(bytes));
export const APPS = ["meshcore", "meshtastic"] as const;
export type App = (typeof APPS)[number];
export type Component = "selector" | App;
export type SharedImage = "selector" | "bootloader" | "partitions";
export type Operation = "install" | "recovery" | Component;
export type ImageKind = "app" | "bootloader" | "partitions";
// Each app's private settings; a downgrade erases them because older firmware
// may not read what a newer version wrote.
export const SETTINGS: Record<App, string[]> = {
  meshcore: ["mc_nvs", "mc_fs"],
  meshtastic: ["mt_nvs", "mt_fs"],
};
// The flasher records the installed version in the last sector of each app
// partition. Coretastic v0.1.0 wrote no record and shipped exactly these.
export const RECORD_FORMAT = "coretastic-app-v1";
export const LEGACY_VERSIONS: Record<App, string> = {
  meshcore: "companion-v1.17.0",
  meshtastic: "v2.7.26.54e0d8d",
};
export interface Image {
  file: string;
  offset: number;
  size: number;
  sha256: string;
}
export interface AppImage extends Image {
  version: string;
  commit: string;
}
export interface Manifest {
  schema: number;
  layout: string;
  version: string;
  chip: string;
  flash_size: number;
  boards: string[];
  partitions: typeof partitions;
  storage_epoch: Record<Component, number>;
  images: Record<SharedImage, Image>;
  apps: Record<App, AppImage[]>;
}
const COMPATIBLE_BACKUP_BOARDS: Record<string, true> = {
  [BOARD]: true,
  "heltec-v4.2-oled": true,
  "heltec-v4.3-oled": true,
};
export function requireCondition(
  condition: unknown,
  message: string,
): asserts condition {
  if (!condition) throw new Error(message);
}
export function partitionBinary(): Uint8Array {
  const data = new Uint8Array(4096).fill(255);
  const view = new DataView(data.buffer);
  const subtypes: Record<string, number> = {
    factory: 0,
    ota_0: 16,
    ota_1: 17,
    nvs: 2,
    ota: 0,
    spiffs: 130,
  };
  partitions.forEach((p, i) => {
    const pos = i * 32;
    data.fill(0, pos, pos + 32);
    view.setUint16(pos, 0x50aa, true);
    data[pos + 2] = p.type === "app" ? 0 : 1;
    data[pos + 3] = subtypes[p.subtype];
    view.setUint32(pos + 4, p.offset, true);
    view.setUint32(pos + 8, p.size, true);
    data.set(new TextEncoder().encode(p.name), pos + 12);
  });
  const pos = partitions.length * 32;
  data[pos] = data[pos + 1] = 0xeb;
  data.set(md5(data.slice(0, pos)), pos + 16);
  return data;
}
export function partition(name: string) {
  const found = partitions.find((p) => p.name === name);
  requireCondition(found, `Unknown partition ${name}.`);
  return found;
}
export const recordOffset = (app: App) =>
  partition(app).offset + partition(app).size - SECTOR;
// Orders upstream tags such as companion-v1.17.1 and v2.7.26.54e0d8d.
export function versionKey(version: unknown): number[] | null {
  const match =
    typeof version === "string" ? /(\d+)\.(\d+)\.(\d+)/.exec(version) : null;
  return match ? match.slice(1).map(Number) : null;
}
function compareKeys(a: number[], b: number[]): number {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}
export function appRecord(app: App, version: string, sha256: string) {
  const data = new Uint8Array(SECTOR).fill(255);
  const json = JSON.stringify({
    format: RECORD_FORMAT,
    component: app,
    version,
    sha256,
  });
  const bytes = new TextEncoder().encode(json);
  data.set(bytes);
  data[bytes.length] = 0;
  return data;
}
// The installed version from its record, or null if it cannot be identified.
export function installedVersion(app: App, record: Uint8Array): string | null {
  if (record[0] === 255) return LEGACY_VERSIONS[app];
  const end = record.indexOf(0);
  let meta: unknown;
  try {
    meta = JSON.parse(
      new TextDecoder().decode(record.subarray(0, end < 0 ? undefined : end)),
    );
  } catch {
    return null;
  }
  if (!meta || typeof meta !== "object") return null;
  const { format, component, version } = meta as Record<string, unknown>;
  return format === RECORD_FORMAT &&
    component === app &&
    typeof version === "string" &&
    versionKey(version)
    ? version
    : null;
}
// Moving to an older (or unidentifiable) version must erase that app's settings.
export function settingsEraseRequired(
  installed: string | null,
  target: string,
): boolean {
  const from = versionKey(installed),
    to = versionKey(target);
  return !from || !to || compareKeys(to, from) < 0;
}
export function selectApp(m: Manifest, app: App, version?: string) {
  const entries = m.apps[app];
  const entry = version
    ? entries.find((e) => e.version === version)
    : entries[0];
  requireCondition(entry, `${app} ${version} is not in this release.`);
  return entry;
}
function checkImage(
  label: string,
  image: Image | undefined,
  file: string,
  offset: number,
  limit: number,
) {
  requireCondition(
    image && image.file === file && image.offset === offset,
    `${label}: invalid filename or address.`,
  );
  requireCondition(
    Number.isSafeInteger(image.size) &&
      image.size > 0 &&
      Math.ceil(image.size / SECTOR) * SECTOR <= limit,
    `${label}: image exceeds partition.`,
  );
  requireCondition(
    typeof image.sha256 === "string" && /^[a-f0-9]{64}$/.test(image.sha256),
    `${label}: invalid checksum.`,
  );
}
export function validateManifest(input: unknown): Manifest {
  requireCondition(
    input && typeof input === "object",
    "Manifest must be an object.",
  );
  const m = input as Manifest;
  requireCondition(
    m.schema === MANIFEST_SCHEMA && m.layout === LAYOUT,
    "Unsupported release layout. Use its matching flasher.",
  );
  requireCondition(
    m.chip === "ESP32-S3" && m.flash_size === FLASH_SIZE,
    "Unsupported target.",
  );
  requireCondition(
    JSON.stringify(m.boards) === JSON.stringify(BOARDS),
    "Unsupported board list.",
  );
  requireCondition(
    JSON.stringify(m.partitions) === JSON.stringify(partitions),
    "Partition map does not match this flasher.",
  );
  requireCondition(
    typeof m.version === "string" && m.version.length > 0,
    "Missing release version.",
  );
  for (const name of ["selector", "meshcore", "meshtastic"] as Component[])
    requireCondition(
      m.storage_epoch?.[name] === 1,
      `Incompatible ${name} settings format.`,
    );
  const shared: Record<SharedImage, [number, number]> = {
    selector: [partition("selector").offset, partition("selector").size],
    bootloader: [0, 0x8000],
    partitions: [0x8000, SECTOR],
  };
  requireCondition(
    m.images &&
      Object.keys(m.images).sort().join() === Object.keys(shared).sort().join(),
    "Missing or unexpected images.",
  );
  for (const [name, [offset, limit]] of Object.entries(shared))
    checkImage(
      name,
      m.images[name as SharedImage],
      `${name}.bin`,
      offset,
      limit,
    );
  requireCondition(
    m.images.partitions.size === SECTOR &&
      m.images.partitions.sha256 === hash(partitionBinary()),
    "Partition checksum does not match layout.",
  );
  requireCondition(
    m.apps && Object.keys(m.apps).sort().join() === [...APPS].sort().join(),
    "Missing or unexpected apps.",
  );
  for (const app of APPS) {
    const entries = m.apps[app];
    requireCondition(
      Array.isArray(entries) && entries.length > 0,
      `${app}: no versions in this release.`,
    );
    const keys: number[][] = [];
    for (const entry of entries) {
      const version = entry?.version;
      requireCondition(
        typeof version === "string" && /^[A-Za-z0-9._-]+$/.test(version),
        `${app}: invalid version.`,
      );
      const key = versionKey(version);
      requireCondition(key, `${app}: unorderable version ${version}.`);
      keys.push(key);
      requireCondition(
        typeof entry.commit === "string" && /^[0-9a-f]{40}$/.test(entry.commit),
        `${app} ${version}: invalid commit.`,
      );
      // The last sector of the partition holds the version record.
      checkImage(
        `${app} ${version}`,
        entry,
        `${app}-${version}.bin`,
        partition(app).offset,
        partition(app).size - SECTOR,
      );
    }
    for (let i = 1; i < keys.length; i++)
      requireCondition(
        compareKeys(keys[i - 1], keys[i]) > 0,
        `${app}: versions must be unique and newest first.`,
      );
  }
  return m;
}
export function validateImage(data: Uint8Array, app: boolean): void {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  requireCondition(
    data.length >= 48 && data[0] === 0xe9 && data[1] > 0 && data[1] <= 16,
    "Invalid ESP image header.",
  );
  requireCondition(
    view.getUint16(12, true) === 9 && data[3] >> 4 === 4 && data[23] === 1,
    "Image must target ESP32-S3, 16 MiB flash, with SHA-256.",
  );
  let pos = 24,
    checksum = 0xef;
  for (let i = 0; i < data[1]; i++) {
    requireCondition(pos + 8 <= data.length, "Truncated image segment.");
    const size = view.getUint32(pos + 4, true);
    pos += 8;
    requireCondition(size <= data.length - pos, "Image segment exceeds file.");
    for (const byte of data.subarray(pos, pos + size)) checksum ^= byte;
    pos += size;
  }
  pos = (Math.floor(pos / 16) + 1) * 16 - 1;
  requireCondition(
    pos + 33 === data.length && data[pos] === checksum,
    "Image checksum/length mismatch.",
  );
  requireCondition(
    hash(data.subarray(0, pos + 1)) === bytesToHex(data.subarray(pos + 1)),
    "Embedded image SHA-256 mismatch.",
  );
  requireCondition(
    !app || view.getUint32(32, true) === 0xabcd5432,
    "Missing application descriptor.",
  );
}
export function validateAsset(image: Image, kind: ImageKind, data: Uint8Array) {
  requireCondition(
    data.length === image.size && hash(data) === image.sha256,
    `${image.file}: download checksum/size mismatch. Retry the download.`,
  );
  if (kind !== "partitions") validateImage(data, kind === "app");
}
export type Versions = Partial<Record<App, string>>;
export interface Planned {
  image: Image;
  kind: ImageKind;
}
// The release images an operation writes, in write order.
export function plannedImages(
  m: Manifest,
  op: Operation,
  versions: Versions = {},
): Planned[] {
  const apps: App[] =
    op === "install"
      ? [...APPS]
      : op === "meshcore" || op === "meshtastic"
        ? [op]
        : [];
  const shared: SharedImage[] =
    op === "install" || op === "recovery"
      ? ["selector", "partitions", "bootloader"]
      : op === "selector"
        ? ["selector"]
        : [];
  return [
    ...apps.map((app) => ({
      image: selectApp(m, app, versions[app]) as Image,
      kind: "app" as ImageKind,
    })),
    ...shared.map((name) => ({
      image: m.images[name],
      kind: (name === "selector" ? "app" : name) as ImageKind,
    })),
  ];
}
export function backup(
  bytes: Uint8Array,
  mac: string,
  board: string,
): Uint8Array {
  requireCondition(bytes.length === FLASH_SIZE, "Backup is incomplete.");
  const metadata = new TextEncoder().encode(
    JSON.stringify({
      format: "coretastic-backup-v1",
      mac,
      board,
      size: bytes.length,
      sha256: hash(bytes),
      created: new Date().toISOString(),
    }),
  );
  const out = new Uint8Array(4096 + bytes.length);
  out.set(metadata);
  out.set(bytes, 4096);
  return out;
}
export function restoreBackup(
  input: Uint8Array,
  mac: string,
  board: string,
): Uint8Array {
  requireCondition(
    input.length === FLASH_SIZE + 4096,
    "Backup has wrong length.",
  );
  const header = input.slice(0, 4096);
  const end = header.indexOf(0);
  requireCondition(end > 0, "Invalid backup metadata.");
  let meta: unknown;
  try {
    meta = JSON.parse(new TextDecoder().decode(header.slice(0, end)));
  } catch {
    meta = undefined;
  }
  requireCondition(
    meta && typeof meta === "object",
    "Invalid backup metadata.",
  );
  const {
    format,
    mac: owner,
    board: origin,
    size,
    sha256,
  } = meta as Record<string, unknown>;
  const bytes = input.slice(4096);
  requireCondition(
    format === "coretastic-backup-v1" &&
      typeof owner === "string" &&
      owner.toLowerCase() === mac.toLowerCase() &&
      typeof origin === "string" &&
      COMPATIBLE_BACKUP_BOARDS[origin] === true &&
      COMPATIBLE_BACKUP_BOARDS[board] === true &&
      size === FLASH_SIZE,
    "Backup belongs to another board or has incompatible metadata.",
  );
  requireCondition(sha256 === hash(bytes), "Backup checksum mismatch.");
  requireCondition(
    hash(bytes.slice(0x8000, 0x9000)) === hash(partitionBinary()),
    "Backup does not contain this dual-boot layout. Use the original firmware recovery tool.",
  );
  return bytes;
}
