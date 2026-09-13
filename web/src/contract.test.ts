import { describe, expect, it } from "vitest";
import { sha256 } from "@noble/hashes/sha2.js";
import {
  BOARD,
  BOARDS,
  FLASH_SIZE,
  LAYOUT,
  backup,
  hash,
  partitionBinary,
  partitions,
  restoreBackup,
  validateImage,
  validateManifest,
} from "./contract";
import type { Manifest, ImageName } from "./contract";
import { program } from "./flasher";

function image(): Uint8Array {
  const bytes = new Uint8Array(336),
    view = new DataView(bytes.buffer);
  bytes.set([0xe9, 1, 2, 0x4f]);
  bytes[12] = 9;
  bytes[23] = 1;
  view.setUint32(28, 256, true);
  view.setUint32(32, 0xabcd5432, true);
  bytes[303] = bytes.slice(32, 288).reduce((a, b) => a ^ b, 0xef);
  bytes.set(sha256(bytes.slice(0, 304)), 304);
  return bytes;
}
function release() {
  const assets = new Map<ImageName, Uint8Array>();
  const images: Manifest["images"] = {} as Manifest["images"];
  for (const [name, offset] of [
    ["selector", 0x10000],
    ["meshcore", 0x100000],
    ["meshtastic", 0x400000],
    ["bootloader", 0],
    ["partitions", 0x8000],
  ] as [ImageName, number][]) {
    const bytes = name === "partitions" ? partitionBinary() : image();
    assets.set(name, bytes);
    images[name] = {
      file: `${name}.bin`,
      offset,
      size: bytes.length,
      sha256: hash(bytes),
    };
  }
  const manifest: Manifest = {
    schema: 1,
    version: "test",
    layout: LAYOUT,
    chip: "ESP32-S3",
    flash_size: FLASH_SIZE,
    boards: BOARDS,
    partitions,
    storage_epoch: { selector: 1, meshcore: 1, meshtastic: 1 },
    images,
    upstream: {
      meshcore: { version: "test", commit: "test" },
      meshtastic: { version: "test", commit: "test" },
    },
  };
  return { manifest, assets };
}
describe("flash contract", () => {
  it("rejects embedded image corruption and wrong chips", () => {
    validateImage(image(), true);
    for (const pos of [0, 12, 28, 40, 303, 320]) {
      const bytes = image();
      bytes[pos] ^= 1;
      expect(() => validateImage(bytes, true)).toThrow();
    }
  });
  it("rejects boundaries, layout edits, and incompatible storage", () => {
    const { manifest } = release();
    validateManifest(manifest);
    const bad = structuredClone(manifest);
    bad.images.meshcore.offset = 0x9000;
    expect(() => validateManifest(bad)).toThrow();
    bad.images.meshcore.offset = 0x100000;
    bad.images.meshcore.size = 0x300001;
    expect(() => validateManifest(bad)).toThrow();
    bad.images.meshcore.size = 336;
    bad.storage_epoch.meshcore = 2;
    expect(() => validateManifest(bad)).toThrow();
  });
  it("never writes when a download or the installed layout is invalid", async () => {
    const { manifest, assets } = release();
    let writes = 0;
    const device = {
      mac: "test",
      read: async () => new Uint8Array(4096),
      write: async () => {
        writes++;
      },
    };
    await expect(program(device, manifest, "meshcore", assets)).rejects.toThrow(
      "partition",
    );
    assets.get("meshtastic")![50] ^= 1;
    await expect(program(device, manifest, "install", assets)).rejects.toThrow(
      "checksum",
    );
    expect(writes).toBe(0);
  });
  it("updates only the selected app plus selector boot metadata without chip erase", async () => {
    const { manifest, assets } = release();
    const addresses: number[] = [];
    const device = {
      mac: "test",
      read: async (address: number) =>
        address === 0 ? image() : partitionBinary(),
      write: async (
        files: { address: number; data: Uint8Array }[],
        erase: boolean,
      ) => {
        expect(erase).toBe(false);
        addresses.push(...files.map((f) => f.address));
      },
    };
    await program(device, manifest, "meshtastic", assets);
    expect(addresses).toEqual([0x400000, 0xe000]);
  });
  it("binds backups to the original device, board, layout, and content", () => {
    const flash = new Uint8Array(FLASH_SIZE).fill(255);
    flash.set(partitionBinary(), 0x8000);
    const saved = backup(flash, "aa:bb:cc:dd:ee:ff", BOARD);
    expect(restoreBackup(saved, "aa:bb:cc:dd:ee:ff", BOARD).length).toBe(
      FLASH_SIZE,
    );
    const legacy = backup(flash, "aa:bb:cc:dd:ee:ff", "heltec-v4.2-oled");
    expect(restoreBackup(legacy, "aa:bb:cc:dd:ee:ff", BOARD).length).toBe(
      FLASH_SIZE,
    );
    expect(() => restoreBackup(saved, "00:00:00:00:00:00", BOARD)).toThrow();
    saved[8000] ^= 1;
    expect(() => restoreBackup(saved, "aa:bb:cc:dd:ee:ff", BOARD)).toThrow(
      "checksum",
    );
  });
});
