import { ESPLoader, Transport } from "esptool-js";
import {
  APPS,
  FLASH_SIZE,
  SECTOR,
  SETTINGS,
  appRecord,
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
  validateAsset,
  validateManifest,
} from "./contract";
import type { App, Manifest, Operation, Versions } from "./contract";
// Native USB Serial/JTAG interface on supported Heltec V4 boards.
const HELTEC_V4_USB_FILTER: SerialPortFilter = {
  usbVendorId: 0x303a,
  usbProductId: 0x1001,
};

function isEspressifUsb(port: SerialPort): boolean {
  const info = port.getInfo();
  return (
    info.usbVendorId === HELTEC_V4_USB_FILTER.usbVendorId &&
    info.usbProductId === HELTEC_V4_USB_FILTER.usbProductId
  );
}

export async function selectEspressifPort(
  serial: Pick<Serial, "getPorts" | "requestPort"> = navigator.serial,
): Promise<SerialPort> {
  const granted = (await serial.getPorts()).filter(isEspressifUsb);
  if (granted.length === 1) return granted[0];
  return serial.requestPort({ filters: [HELTEC_V4_USB_FILTER] });
}

function serialConnectionError(error: unknown): Error {
  if (!(error instanceof DOMException))
    return error instanceof Error ? error : new Error(String(error));
  if (error.name === "NotFoundError")
    return new Error("No Espressif USB serial device was selected.", {
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
export async function inspect(device: Pick<Device, "read">) {
  const layout =
    hash(await device.read(0x8000, SECTOR)) === hash(partitionBinary());
  const installed: Partial<Record<App, string | null>> = {};
  if (layout)
    for (const app of APPS)
      installed[app] = installedVersion(
        app,
        await device.read(recordOffset(app), SECTOR),
      );
  return { layout, installed };
}
export async function program(
  device: Device,
  manifest: Manifest,
  op: Operation,
  assets: Map<string, Uint8Array>,
  versions: Versions = {},
  eraseSettings = false,
) {
  validateManifest(manifest);
  const planned = plannedImages(manifest, op, versions);
  // Validate every byte before the first erase/write, including installed layout.
  for (const { image, kind } of planned) {
    const bytes = assets.get(image.file);
    requireCondition(bytes, `${image.file}: asset missing.`);
    validateAsset(image, kind, bytes);
  }
  if (op !== "install") {
    requireCondition(
      hash(await device.read(0x8000, 4096)) === hash(partitionBinary()),
      "Installed partition table is incompatible. Back up first, then use complete installation.",
    );
    if (op !== "recovery") {
      const boot = manifest.images.bootloader;
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
    const target = selectApp(manifest, app, versions[app]);
    files.push({
      address: recordOffset(app),
      data: appRecord(app, target.version, target.sha256),
    });
    if (op === "install") continue; // The whole chip is erased.
    const installed = installedVersion(
      app,
      await device.read(recordOffset(app), SECTOR),
    );
    requireCondition(
      eraseSettings || !settingsEraseRequired(installed, target.version),
      `${target.version} is older than the installed ${installed ?? "unidentified version"}. Confirm erasing its settings to continue.`,
    );
    if (eraseSettings)
      for (const name of SETTINGS[app]) {
        const { offset, size } = partition(name);
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
    log: (message: string) => void,
    progress: (percent: number) => void,
    disconnected: () => void = () => {},
  ): Promise<UsbDevice> {
    let port: SerialPort;
    try {
      port = await selectEspressifPort();
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
    const device = new UsbDevice(loader, transport, progress, disconnected);
    navigator.serial.addEventListener("disconnect", device.serialDisconnect);
    transport.setDeviceLostCallback(() => device.reportDisconnect());
    try {
      await loader.main();
      requireCondition(
        loader.chip?.CHIP_NAME === "ESP32-S3",
        "Only ESP32-S3 is supported.",
      );
      const id = await loader.readFlashId();
      requireCondition(
        ((id >>> 16) & 255) === 24,
        "Expected 16 MiB flash. Do not flash this board.",
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
        `Validated ESP32-S3, 16 MiB flash, MAC ${device.mac}. Board revision requires your physical check.`,
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
    requireCondition(bytes.length === FLASH_SIZE, "Invalid backup length.");
    await this.write([{ address: 0, data: bytes }], true);
  }
  async disconnect() {
    this.detached = true;
    this.stopWatchingDisconnect();
    await this.transport.disconnect();
  }
}
