import { sha256 } from "@noble/hashes/sha2.js";
import { md5 } from "@noble/hashes/legacy.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const SECTOR = 4096;
export const MANIFEST_SCHEMA = 3;
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
// partition. A board's legacy_versions name what a flash without a record runs
// (Coretastic v0.1.0 wrote none and supported only the Heltec V4).
export const RECORD_FORMAT = "coretastic-app-v1";
export const hash = (bytes: Uint8Array) => bytesToHex(sha256(bytes));
export const md5hex = (bytes: Uint8Array) => bytesToHex(md5(bytes));

export interface BoardProfile {
  id: string;
  name: string;
  experimental: boolean;
  layout: string;
  flash_size: number;
  usb: string[];
  backup_aliases: string[];
  legacy_versions: Partial<Record<App, string>>;
  environments: Record<App, string>;
}
export interface Partition {
  name: string;
  type: string;
  subtype: string;
  offset: number;
  size: number;
}
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
export interface BoardRelease {
  name: string;
  experimental: boolean;
  layout: string;
  flash_size: number;
  partitions: Partition[];
  images: Record<SharedImage, Image>;
  apps: Record<App, AppImage[]>;
}
export interface Manifest {
  schema: number;
  version: string;
  chip: string;
  storage_epoch: Record<Component, number>;
  boards: Record<string, BoardRelease>;
}

export function requireCondition(
  condition: unknown,
  message: string,
): asserts condition {
  if (!condition) throw new Error(message);
}

// Board profiles and partition tables are the same files the Python tooling reads.
const profileFiles = import.meta.glob<Omit<BoardProfile, "id">>(
  "../../boards/*/board.json",
  { eager: true, import: "default" },
);
const tableFiles = import.meta.glob<string>("../../boards/*/partitions.csv", {
  eager: true,
  query: "?raw",
  import: "default",
});
const boardDirectory = (path: string) => path.split("/").at(-2)!;
// Supported boards first; experimental ones last.
export const BOARDS: Record<string, BoardProfile> = Object.fromEntries(
  Object.entries(profileFiles)
    .map(([path, profile]) => [
      boardDirectory(path),
      { ...profile, id: boardDirectory(path) },
    ])
    .sort(
      ([a, pa], [b, pb]) =>
        Number((pa as BoardProfile).experimental) -
          Number((pb as BoardProfile).experimental) ||
        (a as string).localeCompare(b as string),
    ),
);
const TABLES: Record<string, Partition[]> = Object.fromEntries(
  Object.entries(tableFiles).map(([path, csv]) => [
    boardDirectory(path),
    csv
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => {
        const [name, type, subtype, offset, size] = line
          .split(",")
          .map((x) => x.trim());
        return {
          name,
          type,
          subtype,
          offset: Number(offset),
          size: Number(size),
        };
      }),
  ]),
);
export function board(id: string): BoardProfile {
  const profile = BOARDS[id];
  requireCondition(profile, `Unknown board ${id}.`);
  return profile;
}
export function partitions(id: string): Partition[] {
  const table = TABLES[board(id).id];
  requireCondition(table, `${id}: missing partition table.`);
  return table;
}
// Web Serial filters, from board.json "vendor:product" hex pairs.
export function usbFilters(id: string): SerialPortFilter[] {
  return board(id).usb.map((pair) => {
    const [vendor, product] = pair.split(":").map((x) => parseInt(x, 16));
    return { usbVendorId: vendor, usbProductId: product };
  });
}
export function partitionBinary(id: string): Uint8Array {
  const table = partitions(id);
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
  table.forEach((p, i) => {
    const pos = i * 32;
    data.fill(0, pos, pos + 32);
    view.setUint16(pos, 0x50aa, true);
    data[pos + 2] = p.type === "app" ? 0 : 1;
    data[pos + 3] = subtypes[p.subtype];
    view.setUint32(pos + 4, p.offset, true);
    view.setUint32(pos + 8, p.size, true);
    data.set(new TextEncoder().encode(p.name), pos + 12);
  });
  const pos = table.length * 32;
  data[pos] = data[pos + 1] = 0xeb;
  data.set(md5(data.slice(0, pos)), pos + 16);
  return data;
}
export function partition(id: string, name: string): Partition {
  const found = partitions(id).find((p) => p.name === name);
  requireCondition(found, `${id}: unknown partition ${name}.`);
  return found;
}
export const recordOffset = (id: string, app: App) =>
  partition(id, app).offset + partition(id, app).size - SECTOR;
// The image header's flash size field: 2 = 4 MiB, 3 = 8 MiB, 4 = 16 MiB.
export const flashSizeCode = (size: number) => Math.log2(size >> 20);
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
export function installedVersion(
  id: string,
  app: App,
  record: Uint8Array,
): string | null {
  if (record[0] === 255) return board(id).legacy_versions[app] ?? null;
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
export function boardRelease(m: Manifest, id: string): BoardRelease {
  const release = m.boards[id];
  requireCondition(release, `This release has no images for ${id}.`);
  return release;
}
export function selectApp(release: BoardRelease, app: App, version?: string) {
  const entries = release.apps[app];
  const entry = version
    ? entries.find((e) => e.version === version)
    : entries[0];
  requireCondition(entry, `${app} ${version} is not in this release.`);
  return entry;
}
export const boardFile = (id: string, name: string) => `${id}-${name}.bin`;
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
function validateBoard(id: string, release: BoardRelease) {
  const profile = board(id);
  requireCondition(
    release &&
      release.layout === profile.layout &&
      release.flash_size === profile.flash_size,
    `${id}: layout or flash size differs from this flasher.`,
  );
  requireCondition(
    JSON.stringify(release.partitions) === JSON.stringify(partitions(id)),
    `${id}: partition map does not match this flasher.`,
  );
  const selector = partition(id, "selector");
  const shared: Record<SharedImage, [number, number]> = {
    selector: [selector.offset, selector.size],
    bootloader: [0, 0x8000],
    partitions: [0x8000, SECTOR],
  };
  requireCondition(
    release.images &&
      Object.keys(release.images).sort().join() ===
        Object.keys(shared).sort().join(),
    `${id}: missing or unexpected images.`,
  );
  for (const [name, [offset, limit]] of Object.entries(shared))
    checkImage(
      `${id} ${name}`,
      release.images[name as SharedImage],
      boardFile(id, name),
      offset,
      limit,
    );
  requireCondition(
    release.images.partitions.size === SECTOR &&
      release.images.partitions.sha256 === hash(partitionBinary(id)),
    `${id}: partition checksum does not match layout.`,
  );
  requireCondition(
    release.apps &&
      Object.keys(release.apps).sort().join() === [...APPS].sort().join(),
    `${id}: missing or unexpected apps.`,
  );
  for (const app of APPS) {
    const entries = release.apps[app];
    requireCondition(
      Array.isArray(entries) && entries.length > 0,
      `${id} ${app}: no versions in this release.`,
    );
    const keys: number[][] = [];
    for (const entry of entries) {
      const version = entry?.version;
      requireCondition(
        typeof version === "string" && /^[A-Za-z0-9._-]+$/.test(version),
        `${id} ${app}: invalid version.`,
      );
      const key = versionKey(version);
      requireCondition(key, `${id} ${app}: unorderable version ${version}.`);
      keys.push(key);
      requireCondition(
        typeof entry.commit === "string" && /^[0-9a-f]{40}$/.test(entry.commit),
        `${id} ${app} ${version}: invalid commit.`,
      );
      // The last sector of the partition holds the version record.
      checkImage(
        `${id} ${app} ${version}`,
        entry,
        boardFile(id, `${app}-${version}`),
        partition(id, app).offset,
        partition(id, app).size - SECTOR,
      );
    }
    for (let i = 1; i < keys.length; i++)
      requireCondition(
        compareKeys(keys[i - 1], keys[i]) > 0,
        `${id} ${app}: versions must be unique and newest first.`,
      );
  }
}
export function validateManifest(input: unknown): Manifest {
  requireCondition(
    input && typeof input === "object",
    "Manifest must be an object.",
  );
  const m = input as Manifest;
  requireCondition(
    m.schema === MANIFEST_SCHEMA,
    "Unsupported release format. Use the flasher from the same release.",
  );
  requireCondition(m.chip === "ESP32-S3", "Unsupported target.");
  requireCondition(
    typeof m.version === "string" && m.version.length > 0,
    "Missing release version.",
  );
  for (const name of ["selector", "meshcore", "meshtastic"] as Component[])
    requireCondition(
      m.storage_epoch?.[name] === 1,
      `Incompatible ${name} settings format.`,
    );
  requireCondition(
    m.boards &&
      typeof m.boards === "object" &&
      Object.keys(m.boards).length > 0,
    "Release lists no boards.",
  );
  for (const [id, release] of Object.entries(m.boards)) {
    board(id); // Rejects boards this flasher does not know.
    validateBoard(id, release);
  }
  return m;
}
export function validateImage(
  data: Uint8Array,
  app: boolean,
  flashSize = 0x1000000,
): void {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  requireCondition(
    data.length >= 48 && data[0] === 0xe9 && data[1] > 0 && data[1] <= 16,
    "Invalid ESP image header.",
  );
  requireCondition(
    view.getUint16(12, true) === 9 &&
      data[3] >> 4 === flashSizeCode(flashSize) &&
      data[23] === 1,
    `Image must target ESP32-S3, ${flashSize >> 20} MiB flash, with SHA-256.`,
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
export function validateAsset(
  image: Image,
  kind: ImageKind,
  data: Uint8Array,
  flashSize: number,
) {
  requireCondition(
    data.length === image.size && hash(data) === image.sha256,
    `${image.file}: download checksum/size mismatch. Retry the download.`,
  );
  if (kind !== "partitions") validateImage(data, kind === "app", flashSize);
}
export type Versions = Partial<Record<App, string>>;
export interface Planned {
  image: Image;
  kind: ImageKind;
}
// The release images an operation writes, in write order.
export function plannedImages(
  release: BoardRelease,
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
      image: selectApp(release, app, versions[app]) as Image,
      kind: "app" as ImageKind,
    })),
    ...shared.map((name) => ({
      image: release.images[name],
      kind: (name === "selector" ? "app" : name) as ImageKind,
    })),
  ];
}
export function backup(bytes: Uint8Array, mac: string, id: string): Uint8Array {
  requireCondition(
    bytes.length === board(id).flash_size,
    "Backup is incomplete.",
  );
  const metadata = new TextEncoder().encode(
    JSON.stringify({
      format: "coretastic-backup-v1",
      mac,
      board: id,
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
  id: string,
): Uint8Array {
  const size = board(id).flash_size;
  // Backups name the board they came from; older ones used revision-specific names.
  const compatible = [id, ...board(id).backup_aliases];
  requireCondition(
    input.length === size + 4096,
    "Backup has the wrong length for this board.",
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
    size: recorded,
    sha256,
  } = meta as Record<string, unknown>;
  const bytes = input.slice(4096);
  requireCondition(
    format === "coretastic-backup-v1" &&
      typeof owner === "string" &&
      owner.toLowerCase() === mac.toLowerCase() &&
      typeof origin === "string" &&
      compatible.includes(origin) &&
      recorded === size,
    "Backup belongs to another board or has incompatible metadata.",
  );
  requireCondition(sha256 === hash(bytes), "Backup checksum mismatch.");
  requireCondition(
    hash(bytes.slice(0x8000, 0x9000)) === hash(partitionBinary(id)),
    "Backup does not contain this dual-boot layout. Use the original firmware recovery tool.",
  );
  return bytes;
}
