import csv from "../../partitions.csv?raw";
import { sha256 } from "@noble/hashes/sha2.js";
import { md5 } from "@noble/hashes/legacy.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const FLASH_SIZE = 0x1000000;
export const LAYOUT = "heltec-v4-dual-v1";
export const BOARDS = ["heltec-v4.2-oled", "heltec-v4.3-oled"];
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
export type Component = "selector" | "meshcore" | "meshtastic";
export type ImageName = Component | "bootloader" | "partitions";
export type Operation = "install" | "recovery" | Component;
export interface Image {
  file: string;
  offset: number;
  size: number;
  sha256: string;
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
  images: Record<ImageName, Image>;
  upstream: Record<string, { version: string; commit: string }>;
}
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
const expected = Object.fromEntries(
  partitions
    .filter((p) => p.type === "app")
    .map((p) => [p.name, [p.offset, p.size]]),
);
expected.bootloader = [0, 0x8000];
expected.partitions = [0x8000, 4096];
export function validateManifest(input: unknown): Manifest {
  requireCondition(
    input && typeof input === "object",
    "Manifest must be an object.",
  );
  const m = input as Manifest;
  requireCondition(
    m.schema === 1 && m.layout === LAYOUT,
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
  requireCondition(
    m.images &&
      Object.keys(m.images).sort().join() ===
        Object.keys(expected).sort().join(),
    "Missing or unexpected images.",
  );
  for (const [name, [offset, limit]] of Object.entries(expected)) {
    const img = m.images[name as ImageName];
    requireCondition(
      img.file === `${name}.bin` && img.offset === offset,
      `${name}: invalid filename or address.`,
    );
    requireCondition(
      Number.isSafeInteger(img.size) &&
        img.size > 0 &&
        Math.ceil(img.size / 4096) * 4096 <= limit,
      `${name}: image exceeds partition.`,
    );
    requireCondition(
      /^[a-f0-9]{64}$/.test(img.sha256),
      `${name}: invalid checksum.`,
    );
  }
  requireCondition(
    m.images.partitions.size === 4096 &&
      m.images.partitions.sha256 === hash(partitionBinary()),
    "Partition checksum does not match layout.",
  );
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
export function validateAsset(m: Manifest, name: ImageName, data: Uint8Array) {
  const image = m.images[name];
  requireCondition(
    data.length === image.size && hash(data) === image.sha256,
    `${name}: download checksum/size mismatch. Retry the download.`,
  );
  if (name !== "partitions") validateImage(data, name !== "bootloader");
}
export function imageNames(op: Operation): ImageName[] {
  return op === "install"
    ? ["meshcore", "meshtastic", "selector", "partitions", "bootloader"]
    : op === "recovery"
      ? ["selector", "partitions", "bootloader"]
      : [op];
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
  const meta = JSON.parse(new TextDecoder().decode(header.slice(0, end)));
  const bytes = input.slice(4096);
  requireCondition(
    meta.format === "coretastic-backup-v1" &&
      meta.mac.toLowerCase() === mac.toLowerCase() &&
      meta.board === board &&
      meta.size === FLASH_SIZE,
    "Backup belongs to another board or has incompatible metadata.",
  );
  requireCondition(meta.sha256 === hash(bytes), "Backup checksum mismatch.");
  requireCondition(
    hash(bytes.slice(0x8000, 0x9000)) === hash(partitionBinary()),
    "Backup does not contain this dual-boot layout. Use the original firmware recovery tool.",
  );
  return bytes;
}
