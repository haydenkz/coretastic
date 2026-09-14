import { ESPLoader, Transport } from "esptool-js";
import {
  FLASH_SIZE,
  hash,
  md5hex,
  partitionBinary,
  requireCondition,
  validateAsset,
  validateManifest,
  imageNames,
} from "./contract";
import type { Manifest, Operation, ImageName } from "./contract";
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
export async function program(
  device: Device,
  manifest: Manifest,
  op: Operation,
  assets: Map<ImageName, Uint8Array>,
) {
  validateManifest(manifest);
  const names = imageNames(op);
  // Validate every byte before the first erase/write, including installed layout.
  for (const name of names) {
    const bytes = assets.get(name);
    requireCondition(bytes, `${name}: asset missing.`);
    validateAsset(manifest, name, bytes);
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
  const files = names.map((name) => ({
    address: manifest.images[name].offset,
    data: assets.get(name)!,
  }));
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
