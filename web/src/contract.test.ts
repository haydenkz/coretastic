import { describe, expect, it } from "vitest";
import { sha256 } from "@noble/hashes/sha2.js";
import {
  BOARD,
  BOARDS,
  FLASH_SIZE,
  LAYOUT,
  MANIFEST_SCHEMA,
  SECTOR,
  appRecord,
  backup,
  hash,
  installedVersion,
  partition,
  partitionBinary,
  partitions,
  recordOffset,
  restoreBackup,
  settingsEraseRequired,
  validateImage,
  validateManifest,
  versionKey,
} from "./contract";
import type { App, AppImage, Image, Manifest, SharedImage } from "./contract";
import { inspect, program, selectEspressifPort } from "./flasher";

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
  const assets = new Map<string, Uint8Array>();
  const add = (file: string, offset: number, bytes = image()): Image => {
    assets.set(file, bytes);
    return { file, offset, size: bytes.length, sha256: hash(bytes) };
  };
  const images = {} as Record<SharedImage, Image>;
  for (const [name, offset] of [
    ["selector", 0x10000],
    ["bootloader", 0],
    ["partitions", 0x8000],
  ] as [SharedImage, number][])
    images[name] = add(
      `${name}.bin`,
      offset,
      name === "partitions" ? partitionBinary() : image(),
    );
  const app = (component: App, version: string): AppImage => ({
    version,
    commit: "c".repeat(40),
    ...add(`${component}-${version}.bin`, partition(component).offset),
  });
  const manifest: Manifest = {
    schema: MANIFEST_SCHEMA,
    version: "test",
    layout: LAYOUT,
    chip: "ESP32-S3",
    flash_size: FLASH_SIZE,
    boards: BOARDS,
    partitions,
    storage_epoch: { selector: 1, meshcore: 1, meshtastic: 1 },
    images,
    apps: {
      meshcore: [
        app("meshcore", "companion-v1.17.1"),
        app("meshcore", "companion-v1.16.0"),
      ],
      meshtastic: [app("meshtastic", "v2.7.26.54e0d8d")],
    },
  };
  return { manifest, assets };
}
// A dual-boot device with the given version records (0xFF = none written).
function installedDevice(
  records: Partial<Record<App, Uint8Array>> = {},
  writes: { address: number; data: Uint8Array }[][] = [],
) {
  return {
    mac: "test",
    read: async (address: number, size: number) => {
      if (address === 0) return image();
      if (address === 0x8000) return partitionBinary();
      for (const app of ["meshcore", "meshtastic"] as App[])
        if (address === recordOffset(app))
          return records[app] ?? new Uint8Array(SECTOR).fill(255);
      throw new Error(`unexpected read ${address.toString(16)} ${size}`);
    },
    write: async (
      files: { address: number; data: Uint8Array }[],
      erase: boolean,
    ) => {
      expect(erase).toBe(false);
      writes.push(files);
    },
  };
}
describe("USB serial selection", () => {
  const port = (vendor = 0x303a, product = 0x1001) =>
    ({
      getInfo: () => ({ usbVendorId: vendor, usbProductId: product }),
    }) as SerialPort;

  it("reuses one granted Espressif port without reopening the chooser", async () => {
    const granted = port();
    let requests = 0;
    const selected = await selectEspressifPort({
      getPorts: async () => [granted],
      requestPort: async () => {
        requests++;
        return port();
      },
    });
    expect(selected).toBe(granted);
    expect(requests).toBe(0);
  });

  it("filters the chooser to the Heltec ESP32-S3 USB interface", async () => {
    const selectedPort = port();
    let options: SerialPortRequestOptions | undefined;
    const selected = await selectEspressifPort({
      getPorts: async () => [port(0x1234, 0x5678)],
      requestPort: async (value) => {
        options = value;
        return selectedPort;
      },
    });
    expect(selected).toBe(selectedPort);
    expect(options).toEqual({
      filters: [{ usbVendorId: 0x303a, usbProductId: 0x1001 }],
    });
  });
});

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
    bad.apps.meshcore[0].offset = 0x9000;
    expect(() => validateManifest(bad)).toThrow();
    bad.apps.meshcore[0].offset = 0x100000;
    // The last sector of the partition is reserved for the version record.
    bad.apps.meshcore[0].size = 0x300000 - SECTOR + 1;
    expect(() => validateManifest(bad)).toThrow("exceeds");
    bad.apps.meshcore[0].size = 336;
    bad.storage_epoch.meshcore = 2;
    expect(() => validateManifest(bad)).toThrow();
  });
  it("requires ordered, well-formed app versions", () => {
    const { manifest } = release();
    const mutations: ((m: Manifest) => unknown)[] = [
      (m) => m.apps.meshcore.reverse(),
      (m) => m.apps.meshcore.push(m.apps.meshcore[0]),
      (m) => (m.apps.meshcore = []),
      (m) => delete (m.apps as Partial<Manifest["apps"]>).meshtastic,
      (m) => (m.apps.meshcore[0].version = "latest"),
      (m) => (m.apps.meshcore[0].version = "../1.2.3"),
      (m) => (m.apps.meshcore[0].commit = "main"),
      (m) => (m.schema = 1),
    ];
    for (const [index, mutate] of mutations.entries()) {
      const bad = structuredClone(manifest);
      mutate(bad);
      expect(() => validateManifest(bad), `mutation ${index}`).toThrow();
    }
  });
  it("records installed versions and orders them", () => {
    expect(versionKey("companion-v1.17.1")).toEqual([1, 17, 1]);
    expect(versionKey("v2.7.26.54e0d8d")).toEqual([2, 7, 26]);
    expect(versionKey("latest")).toBeNull();
    expect(recordOffset("meshcore")).toBe(0x3ff000);
    expect(recordOffset("meshtastic")).toBe(0x9ff000);
    const record = appRecord("meshcore", "companion-v1.17.1", "a".repeat(64));
    expect(record.length).toBe(SECTOR);
    expect(installedVersion("meshcore", record)).toBe("companion-v1.17.1");
    expect(installedVersion("meshtastic", record)).toBeNull();
    // v0.1.0 wrote no record and shipped exactly one version of each app.
    const blank = new Uint8Array(SECTOR).fill(255);
    expect(installedVersion("meshcore", blank)).toBe("companion-v1.17.0");
    expect(installedVersion("meshtastic", blank)).toBe("v2.7.26.54e0d8d");
    expect(
      settingsEraseRequired("companion-v1.17.1", "companion-v1.16.0"),
    ).toBe(true);
    expect(
      settingsEraseRequired("companion-v1.17.0", "companion-v1.17.1"),
    ).toBe(false);
    expect(settingsEraseRequired(null, "companion-v1.17.1")).toBe(true);
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
    assets.get("meshtastic-v2.7.26.54e0d8d.bin")![50] ^= 1;
    await expect(program(device, manifest, "install", assets)).rejects.toThrow(
      "checksum",
    );
    expect(writes).toBe(0);
  });
  it("updates only the selected app, its record, and boot metadata", async () => {
    const { manifest, assets } = release();
    const writes: { address: number; data: Uint8Array }[][] = [];
    await program(installedDevice({}, writes), manifest, "meshtastic", assets);
    expect(writes[0].map((f) => f.address)).toEqual([
      0x400000,
      recordOffset("meshtastic"),
      0xe000,
    ]);
    expect(installedVersion("meshtastic", writes[0][1].data)).toBe(
      "v2.7.26.54e0d8d",
    );
  });
  it("installs a chosen version and erases settings only on downgrade", async () => {
    const { manifest, assets } = release();
    const newest = {
      meshcore: appRecord("meshcore", "companion-v1.17.1", "a".repeat(64)),
    };
    const older = { meshcore: "companion-v1.16.0" };
    const writes: { address: number; data: Uint8Array }[][] = [];
    await expect(
      program(installedDevice(newest, writes), manifest, "meshcore", assets, {
        meshcore: "companion-v9.9.9",
      }),
    ).rejects.toThrow("not in this release");
    await expect(
      program(
        installedDevice(newest, writes),
        manifest,
        "meshcore",
        assets,
        older,
      ),
    ).rejects.toThrow("older than the installed companion-v1.17.1");
    expect(writes).toEqual([]);
    await program(
      installedDevice(newest, writes),
      manifest,
      "meshcore",
      assets,
      older,
      true,
    );
    const written = new Map(writes[0].map((f) => [f.address, f.data]));
    expect(written.get(0x100000)).toBe(
      assets.get("meshcore-companion-v1.16.0.bin"),
    );
    expect(
      installedVersion("meshcore", written.get(recordOffset("meshcore"))!),
    ).toBe("companion-v1.16.0");
    for (const name of ["mc_nvs", "mc_fs"]) {
      const { offset, size } = partition(name);
      expect(written.get(offset)).toEqual(new Uint8Array(size).fill(255));
    }
    expect(written.has(partition("mt_nvs").offset)).toBe(false);
    // Upgrading from a legacy v0.1.0 install keeps settings.
    writes.length = 0;
    await program(installedDevice({}, writes), manifest, "meshcore", assets);
    expect(writes[0].map((f) => f.address)).toEqual([
      0x100000,
      recordOffset("meshcore"),
      0xe000,
    ]);
    expect(
      await inspect(installedDevice({ meshcore: new Uint8Array(SECTOR) })),
    ).toEqual({
      layout: true,
      installed: { meshcore: null, meshtastic: "v2.7.26.54e0d8d" },
    });
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
    for (const header of ["not json", "[1]", '{"mac": 1}', "{}"]) {
      const input = new Uint8Array(4096 + FLASH_SIZE);
      input.set(new TextEncoder().encode(header));
      expect(() => restoreBackup(input, "aa:bb:cc:dd:ee:ff", BOARD)).toThrow(
        /metadata/,
      );
    }
  });
});
