import { describe, expect, it } from "vitest";
import { sha256 } from "@noble/hashes/sha2.js";
import {
  BOARDS,
  MANIFEST_SCHEMA,
  SECTOR,
  appRecord,
  backup,
  board,
  boardFile,
  flashSizeCode,
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
import type {
  App,
  AppImage,
  BoardRelease,
  Image,
  Manifest,
  SharedImage,
} from "./contract";
import { inspect, program, selectBoardPort } from "./flasher";

const V4 = "heltec-v4-oled";
const V3 = "heltec-v3";

function image(id = V4): Uint8Array {
  const bytes = new Uint8Array(336),
    view = new DataView(bytes.buffer);
  bytes.set([0xe9, 1, 2, (flashSizeCode(board(id).flash_size) << 4) | 0xf]);
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
  const add = (file: string, offset: number, bytes: Uint8Array): Image => {
    assets.set(file, bytes);
    return { file, offset, size: bytes.length, sha256: hash(bytes) };
  };
  const boardRelease = (id: string): BoardRelease => {
    const images = {} as Record<SharedImage, Image>;
    for (const [name, offset] of [
      ["selector", partition(id, "selector").offset],
      ["bootloader", 0],
      ["partitions", 0x8000],
    ] as [SharedImage, number][])
      images[name] = add(
        boardFile(id, name),
        offset,
        name === "partitions" ? partitionBinary(id) : image(id),
      );
    const app = (component: App, version: string): AppImage => ({
      version,
      commit: "c".repeat(40),
      ...add(
        boardFile(id, `${component}-${version}`),
        partition(id, component).offset,
        image(id),
      ),
    });
    return {
      name: board(id).name,
      experimental: board(id).experimental,
      layout: board(id).layout,
      flash_size: board(id).flash_size,
      partitions: partitions(id),
      images,
      apps: {
        meshcore: [
          app("meshcore", "companion-v1.17.1"),
          app("meshcore", "companion-v1.16.0"),
        ],
        meshtastic: [app("meshtastic", "v2.7.26.54e0d8d")],
      },
    };
  };
  const manifest: Manifest = {
    schema: MANIFEST_SCHEMA,
    version: "test",
    chip: "ESP32-S3",
    storage_epoch: { selector: 1, meshcore: 1, meshtastic: 1 },
    boards: { [V4]: boardRelease(V4), [V3]: boardRelease(V3) },
  };
  return { manifest, assets };
}
// A dual-boot device with the given version records (0xFF = none written).
function installedDevice(
  records: Partial<Record<App, Uint8Array>> = {},
  writes: { address: number; data: Uint8Array }[][] = [],
  id = V4,
) {
  return {
    mac: "test",
    read: async (address: number, size: number) => {
      if (address === 0) return image(id);
      if (address === 0x8000) return partitionBinary(id);
      for (const app of ["meshcore", "meshtastic"] as App[])
        if (address === recordOffset(id, app))
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

  it("reuses one granted port for the board without reopening the chooser", async () => {
    const granted = port();
    let requests = 0;
    const selected = await selectBoardPort(V4, {
      getPorts: async () => [granted],
      requestPort: async () => {
        requests++;
        return port();
      },
    });
    expect(selected).toBe(granted);
    expect(requests).toBe(0);
  });

  it("filters the chooser to the selected board's USB interface", async () => {
    for (const [id, filters] of [
      [V4, [{ usbVendorId: 0x303a, usbProductId: 0x1001 }]],
      [V3, [{ usbVendorId: 0x10c4, usbProductId: 0xea60 }]],
    ] as const) {
      const selectedPort = port();
      let options: SerialPortRequestOptions | undefined;
      const selected = await selectBoardPort(id, {
        // A granted V4 port is not reused for a V3, and vice versa.
        getPorts: async () => [
          id === V4 ? port(0x10c4, 0xea60) : port(0x303a, 0x1001),
        ],
        requestPort: async (value) => {
          options = value;
          return selectedPort;
        },
      });
      expect(selected).toBe(selectedPort);
      expect(options).toEqual({ filters });
    }
  });
});

describe("flash contract", () => {
  it("knows supported boards before experimental ones", () => {
    expect(Object.keys(BOARDS)).toEqual([V4, V3]);
    expect(BOARDS[V3].experimental).toBe(true);
    expect(board(V3).flash_size).toBe(0x800000);
    expect(() => board("heltec-v9")).toThrow("Unknown board");
    expect(hash(partitionBinary(V3))).not.toBe(hash(partitionBinary(V4)));
  });
  it("rejects embedded image corruption, wrong chips, and wrong flash sizes", () => {
    validateImage(image(), true);
    for (const pos of [0, 12, 28, 40, 303, 320]) {
      const bytes = image();
      bytes[pos] ^= 1;
      expect(() => validateImage(bytes, true)).toThrow();
    }
    validateImage(image(V3), true, 0x800000);
    expect(() => validateImage(image(V4), true, 0x800000)).toThrow("8 MiB");
  });
  it("rejects boundaries, layout edits, and incompatible storage", () => {
    const { manifest } = release();
    validateManifest(manifest);
    const bad = structuredClone(manifest);
    const meshcore = bad.boards[V4].apps.meshcore[0];
    meshcore.offset = 0x9000;
    expect(() => validateManifest(bad)).toThrow();
    meshcore.offset = 0x100000;
    // The last sector of the partition is reserved for the version record.
    meshcore.size = 0x300000 - SECTOR + 1;
    expect(() => validateManifest(bad)).toThrow("exceeds");
    meshcore.size = 336;
    bad.storage_epoch.meshcore = 2;
    expect(() => validateManifest(bad)).toThrow();
    // One board's layout cannot stand in for another's.
    const swapped = structuredClone(manifest);
    swapped.boards[V3].partitions = partitions(V4);
    expect(() => validateManifest(swapped)).toThrow("partition map");
    const unknown = structuredClone(manifest);
    unknown.boards["heltec-v9"] = unknown.boards[V4];
    expect(() => validateManifest(unknown)).toThrow("Unknown board");
    // A release may ship a subset of known boards.
    const subset = structuredClone(manifest);
    delete subset.boards[V3];
    validateManifest(subset);
  });
  it("requires ordered, well-formed app versions", () => {
    const { manifest } = release();
    const mutations: ((m: Manifest) => unknown)[] = [
      (m) => m.boards[V4].apps.meshcore.reverse(),
      (m) => m.boards[V4].apps.meshcore.push(m.boards[V4].apps.meshcore[0]),
      (m) => (m.boards[V4].apps.meshcore = []),
      (m) =>
        delete (m.boards[V4].apps as Partial<BoardRelease["apps"]>).meshtastic,
      (m) => (m.boards[V4].apps.meshcore[0].version = "latest"),
      (m) => (m.boards[V4].apps.meshcore[0].version = "../1.2.3"),
      (m) => (m.boards[V4].apps.meshcore[0].commit = "main"),
      (m) => (m.boards = {}),
      (m) => (m.schema = 2),
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
    expect(recordOffset(V4, "meshcore")).toBe(0x3ff000);
    expect(recordOffset(V4, "meshtastic")).toBe(0x9ff000);
    expect(recordOffset(V3, "meshcore")).toBe(0x25f000);
    expect(recordOffset(V3, "meshtastic")).toBe(0x55f000);
    const record = appRecord("meshcore", "companion-v1.17.1", "a".repeat(64));
    expect(record.length).toBe(SECTOR);
    expect(installedVersion(V4, "meshcore", record)).toBe("companion-v1.17.1");
    expect(installedVersion(V4, "meshtastic", record)).toBeNull();
    // v0.1.0 wrote no record, shipped one version of each app, and only for the V4.
    const blank = new Uint8Array(SECTOR).fill(255);
    expect(installedVersion(V4, "meshcore", blank)).toBe("companion-v1.17.0");
    expect(installedVersion(V4, "meshtastic", blank)).toBe("v2.7.26.54e0d8d");
    expect(installedVersion(V3, "meshcore", blank)).toBeNull();
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
    await expect(
      program(device, manifest, V4, "meshcore", assets),
    ).rejects.toThrow("partition");
    assets.get(boardFile(V4, "meshtastic-v2.7.26.54e0d8d"))![50] ^= 1;
    await expect(
      program(device, manifest, V4, "install", assets),
    ).rejects.toThrow("checksum");
    // A V4 in the V3 flow fails the V3 layout check.
    await expect(
      program(installedDevice(), manifest, V3, "meshcore", assets),
    ).rejects.toThrow("partition");
    expect(writes).toBe(0);
  });
  it("updates only the selected app, its record, and boot metadata", async () => {
    const { manifest, assets } = release();
    const writes: { address: number; data: Uint8Array }[][] = [];
    await program(
      installedDevice({}, writes),
      manifest,
      V4,
      "meshtastic",
      assets,
    );
    expect(writes[0].map((f) => f.address)).toEqual([
      0x400000,
      recordOffset(V4, "meshtastic"),
      0xe000,
    ]);
    expect(installedVersion(V4, "meshtastic", writes[0][1].data)).toBe(
      "v2.7.26.54e0d8d",
    );
  });
  it("writes the V3's own images and offsets", async () => {
    const { manifest, assets } = release();
    const record = {
      meshtastic: appRecord("meshtastic", "v2.7.26.54e0d8d", "a".repeat(64)),
    };
    const writes: { address: number; data: Uint8Array }[][] = [];
    await program(
      installedDevice(record, writes, V3),
      manifest,
      V3,
      "meshtastic",
      assets,
    );
    expect(writes[0].map((f) => f.address)).toEqual([
      0x260000, 0x55f000, 0xe000,
    ]);
    expect(writes[0][0].data).toBe(
      assets.get(boardFile(V3, "meshtastic-v2.7.26.54e0d8d")),
    );
    // Without a record the installed version is unknown, so settings must go.
    await expect(
      program(installedDevice({}, [], V3), manifest, V3, "meshcore", assets),
    ).rejects.toThrow("unidentified version");
  });
  it("installs a chosen version and erases settings only on downgrade", async () => {
    const { manifest, assets } = release();
    const newest = {
      meshcore: appRecord("meshcore", "companion-v1.17.1", "a".repeat(64)),
    };
    const older = { meshcore: "companion-v1.16.0" };
    const writes: { address: number; data: Uint8Array }[][] = [];
    await expect(
      program(
        installedDevice(newest, writes),
        manifest,
        V4,
        "meshcore",
        assets,
        { meshcore: "companion-v9.9.9" },
      ),
    ).rejects.toThrow("not in this release");
    await expect(
      program(
        installedDevice(newest, writes),
        manifest,
        V4,
        "meshcore",
        assets,
        older,
      ),
    ).rejects.toThrow("older than the installed companion-v1.17.1");
    expect(writes).toEqual([]);
    await program(
      installedDevice(newest, writes),
      manifest,
      V4,
      "meshcore",
      assets,
      older,
      true,
    );
    const written = new Map(writes[0].map((f) => [f.address, f.data]));
    expect(written.get(0x100000)).toBe(
      assets.get(boardFile(V4, "meshcore-companion-v1.16.0")),
    );
    expect(
      installedVersion(
        V4,
        "meshcore",
        written.get(recordOffset(V4, "meshcore"))!,
      ),
    ).toBe("companion-v1.16.0");
    for (const name of ["mc_nvs", "mc_fs"]) {
      const { offset, size } = partition(V4, name);
      expect(written.get(offset)).toEqual(new Uint8Array(size).fill(255));
    }
    expect(written.has(partition(V4, "mt_nvs").offset)).toBe(false);
    // Upgrading from a legacy v0.1.0 install keeps settings.
    writes.length = 0;
    await program(
      installedDevice({}, writes),
      manifest,
      V4,
      "meshcore",
      assets,
    );
    expect(writes[0].map((f) => f.address)).toEqual([
      0x100000,
      recordOffset(V4, "meshcore"),
      0xe000,
    ]);
    expect(
      await inspect(installedDevice({ meshcore: new Uint8Array(SECTOR) }), V4),
    ).toEqual({
      layout: true,
      installed: { meshcore: null, meshtastic: "v2.7.26.54e0d8d" },
    });
    expect(await inspect(installedDevice(), V3)).toEqual({
      layout: false,
      installed: {},
    });
  });
  it("binds backups to the original device, board, layout, and content", () => {
    const size = board(V4).flash_size;
    const flash = new Uint8Array(size).fill(255);
    flash.set(partitionBinary(V4), 0x8000);
    const saved = backup(flash, "aa:bb:cc:dd:ee:ff", V4);
    expect(restoreBackup(saved, "aa:bb:cc:dd:ee:ff", V4).length).toBe(size);
    // Earlier flashers recorded the V4 revision instead of the board id.
    const header = new TextDecoder().decode(saved.slice(0, saved.indexOf(0)));
    const legacy = new Uint8Array(saved.length);
    legacy.set(
      new TextEncoder().encode(
        JSON.stringify({ ...JSON.parse(header), board: "heltec-v4.2-oled" }),
      ),
    );
    legacy.set(saved.slice(4096), 4096);
    expect(restoreBackup(legacy, "aa:bb:cc:dd:ee:ff", V4).length).toBe(size);
    expect(() => restoreBackup(saved, "00:00:00:00:00:00", V4)).toThrow();
    // A V4 backup is the wrong size for a V3.
    expect(() => restoreBackup(saved, "aa:bb:cc:dd:ee:ff", V3)).toThrow(
      "length",
    );
    saved[8000] ^= 1;
    expect(() => restoreBackup(saved, "aa:bb:cc:dd:ee:ff", V4)).toThrow(
      "checksum",
    );
    for (const header of ["not json", "[1]", '{"mac": 1}', "{}"]) {
      const input = new Uint8Array(4096 + size);
      input.set(new TextEncoder().encode(header));
      expect(() => restoreBackup(input, "aa:bb:cc:dd:ee:ff", V4)).toThrow(
        /metadata/,
      );
    }
  });
});
