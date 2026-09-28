import { ESPLoader, Transport } from "esptool-js";
import {
  APPS,
  SECTOR,
  SETTINGS,
  appRecord,
  board,
  boardRelease,
  hash,
  installedVersion,
  md5hex,
  partition,
  partitionBinary,
  plannedImages,
  recordOffset,
  requireCondition,
  selectApp,
  settingsEraseRequired,
  usbFilters,
  validateAsset,
  validateManifest,
} from "./contract";
import type { App, Manifest, Operation, Versions } from "./contract";

// Only offer the USB interfaces the chosen board exposes (native USB on the V4,
// a CP2102 bridge on the V3).
export async function selectBoardPort(
  id: string,
  serial: Pick<Serial, "getPorts" | "requestPort"> = navigator.serial,
): Promise<SerialPort> {
  const filters = usbFilters(id);
  const matches = (port: SerialPort) => {
    const info = port.getInfo();
    return filters.some(
      (f) =>
        info.usbVendorId === f.usbVendorId &&
        info.usbProductId === f.usbProductId,
    );
  };
  const granted = (await serial.getPorts()).filter(matches);
  if (granted.length === 1) return granted[0];
  return serial.requestPort({ filters });
}

function serialConnectionError(error: unknown): Error {
  if (!(error instanceof DOMException))
    return error instanceof Error ? error : new Error(String(error));
  if (error.name === "NotFoundError")
    return new Error("No matching USB serial device was selected.", {
      cause: error,
    });
  if (error.name === "NetworkError" || error.name === "InvalidStateError")
    return new Error(
      "Could not open the USB serial port. On Linux, close serial monitors and verify access to /dev/ttyACM0.",
      { cause: error },
    );
  return error;
}

export interface Device {
  mac: string;
  read(address: number, size: number): Promise<Uint8Array>;
  write(
    files: { address: number; data: Uint8Array }[],
    eraseAll: boolean,
  ): Promise<void>;
}
// What a connected device has installed; versions are only meaningful when
// the partition table is the dual-boot layout.
export async function inspect(device: Pick<Device, "read">, id: string) {
  const layout =
    hash(await device.read(0x8000, SECTOR)) === hash(partitionBinary(id));
  const installed: Partial<Record<App, string | null>> = {};
  if (layout)
    for (const app of APPS)
      installed[app] = installedVersion(
        id,
        app,
        await device.read(recordOffset(id, app), SECTOR),
      );
  return { layout, installed };
}
export async function program(
  device: Device,
  manifest: Manifest,
  id: string,
  op: Operation,
  assets: Map<string, Uint8Array>,
  versions: Versions = {},
  eraseSettings = false,
) {
  validateManifest(manifest);
  const release = boardRelease(manifest, id);
  const planned = plannedImages(release, op, versions);
  // Validate every byte before the first erase/write, including installed layout.
  for (const { image, kind } of planned) {
    const bytes = assets.get(image.file);
    requireCondition(bytes, `${image.file}: asset missing.`);
    validateAsset(image, kind, bytes, board(id).flash_size);
  }
  if (op !== "install") {
    requireCondition(
      hash(await device.read(0x8000, 4096)) === hash(partitionBinary(id)),
      "Installed partition table is incompatible. Back up first, then use complete installation.",
    );
    if (op !== "recovery") {
      const boot = release.images.bootloader;
      requireCondition(
        hash(await device.read(0, boot.size)) === boot.sha256,
        "Installed bootloader differs. Run selector/bootloader recovery first.",
      );
    }
  }
  const files = planned.map(({ image }) => ({
    address: image.offset,
    data: assets.get(image.file)!,
  }));
  for (const app of APPS) {
    if (op !== "install" && op !== app) continue;
    const target = selectApp(release, app, versions[app]);
    files.push({
      address: recordOffset(id, app),
      data: appRecord(app, target.version, target.sha256),
    });
    if (op === "install") continue; // The whole chip is erased.
    const installed = installedVersion(
      id,
      app,
      await device.read(recordOffset(id, app), SECTOR),
    );
    requireCondition(
      eraseSettings || !settingsEraseRequired(installed, target.version),
      `${target.version} is older than the installed ${installed ?? "unidentified version"}. Confirm erasing its settings to continue.`,
    );
    if (eraseSettings)
      for (const name of SETTINGS[app]) {
        const { offset, size } = partition(id, name);
        files.push({ address: offset, data: new Uint8Array(size).fill(255) });
      }
  }
  // Erased OTA metadata returns to the factory selector after a USB operation.
  files.push({ address: 0xe000, data: new Uint8Array(8192).fill(255) });
  await device.write(files, op === "install");
}
export class UsbDevice implements Device {
  mac = "";
  private detached = false;
  private readonly serialDisconnect = (event: Event) => {
    if (event.target === this.transport.device) this.reportDisconnect();
  };
  constructor(
    readonly loader: ESPLoader,
    readonly transport: Transport,
    readonly board: string,
    readonly progress: (percent: number) => void,
    private readonly disconnected: () => void,
  ) {}

  private reportDisconnect() {
    if (this.detached) return;
    this.detached = true;
    this.stopWatchingDisconnect();
    this.disconnected();
  }

  private stopWatchingDisconnect() {
    navigator.serial.removeEventListener("disconnect", this.serialDisconnect);
    this.transport.setDeviceLostCallback(null);
  }

  static async connect(
    boardId: string,
    log: (message: string) => void,
    progress: (percent: number) => void,
    disconnected: () => void = () => {},
  ): Promise<UsbDevice> {
    const profile = board(boardId);
    const mib = profile.flash_size >> 20;
    let port: SerialPort;
    try {
      port = await selectBoardPort(boardId);
    } catch (error) {
      throw serialConnectionError(error);
    }
    const info = port.getInfo();
    log(
      `Selected USB serial ${info.usbVendorId?.toString(16).padStart(4, "0")}:${info.usbProductId?.toString(16).padStart(4, "0")}.`,
    );
    const transport = new Transport(port, false);
    const loader = new ESPLoader({
      transport,
      baudrate: 460800,
      terminal: { clean() {}, write: log, writeLine: log },
    });
    const device = new UsbDevice(
      loader,
      transport,
      boardId,
      progress,
      disconnected,
    );
    navigator.serial.addEventListener("disconnect", device.serialDisconnect);
    transport.setDeviceLostCallback(() => device.reportDisconnect());
    try {
      await loader.main();
      requireCondition(
        loader.chip?.CHIP_NAME === "ESP32-S3",
        "Only ESP32-S3 is supported.",
      );
      // The JEDEC capacity byte is log2 of the size in bytes.
      const id = await loader.readFlashId();
      requireCondition(
        ((id >>> 16) & 255) === Math.log2(profile.flash_size),
        `Expected ${mib} MiB flash for ${profile.name}. Check the board you selected.`,
      );
      // ESP32-S3 eFuse definitions from Espressif esptool 4.8.1.
      const crypt = ((await loader.readReg(0x60007034)) >>> 18) & 7;
      const encrypted =
        ((crypt & 1) + ((crypt >> 1) & 1) + ((crypt >> 2) & 1)) % 2;
      const secure = (await loader.readReg(0x60007038)) & (1 << 20);
      requireCondition(
        !encrypted && !secure,
        "Secure Boot or flash encryption is enabled; this utility cannot service this device.",
      );
      device.mac = await loader.chip.readMac(loader);
      log(
        `Validated ESP32-S3, ${mib} MiB flash, MAC ${device.mac}. ${profile.name} requires your physical check.`,
      );
      return device;
    } catch (error) {
      device.stopWatchingDisconnect();
      await transport.disconnect().catch(() => {});
      throw serialConnectionError(error);
    }
  }
  async read(address: number, size: number) {
    return this.loader.readFlash(address, size, (_packet, read, total) =>
      this.progress((read / total) * 100),
    );
  }
  async write(
    files: { address: number; data: Uint8Array }[],
    eraseAll: boolean,
  ) {
    const total = files.reduce((sum, f) => sum + f.data.length, 0);
    await this.loader.writeFlash({
      fileArray: files,
      eraseAll,
      compress: true,
      flashSize: "keep",
      flashMode: "keep",
      flashFreq: "keep",
      calculateMD5Hash: md5hex,
      reportProgress: (index, written) =>
        this.progress(
          ((files.slice(0, index).reduce((sum, f) => sum + f.data.length, 0) +
            written) /
            total) *
            100,
        ),
    });
    // Independently request the stub's flash digest for each completed range.
    for (const file of files)
      requireCondition(
        (
          await this.loader.flashMd5sum(file.address, file.data.length)
        ).toLowerCase() === md5hex(file.data),
        "Read-back verification failed. Keep the device in USB recovery and retry.",
      );
  }
  async restore(bytes: Uint8Array) {
    requireCondition(
      bytes.length === board(this.board).flash_size,
      "Invalid backup length.",
    );
    await this.write([{ address: 0, data: bytes }], true);
  }
  async disconnect() {
    this.detached = true;
    this.stopWatchingDisconnect();
    await this.transport.disconnect();
  }
}
