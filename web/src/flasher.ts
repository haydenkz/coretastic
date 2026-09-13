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
  constructor(
    readonly loader: ESPLoader,
    readonly transport: Transport,
    readonly progress: (percent: number) => void,
  ) {}
  static async connect(
    log: (message: string) => void,
    progress: (percent: number) => void,
  ): Promise<UsbDevice> {
    const port = await navigator.serial.requestPort();
    const transport = new Transport(port, true);
    const loader = new ESPLoader({
      transport,
      baudrate: 460800,
      terminal: { clean() {}, write: log, writeLine: log },
    });
    const device = new UsbDevice(loader, transport, progress);
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
      await transport.disconnect().catch(() => {});
      throw error;
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
    await this.transport.disconnect();
  }
}
