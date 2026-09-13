import "./style.css";
import {
  backup,
  restoreBackup,
  imageNames,
  validateManifest,
} from "./contract";
import type { ImageName, Manifest, Operation } from "./contract";
import { UsbDevice, program } from "./flasher";
const el = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const status = el("status"),
  logBox = el("log");
const logs: string[] = [];
let device: UsbDevice | undefined,
  manifest: Manifest | undefined,
  busy = false;
const board = el<HTMLSelectElement>("board"),
  operation = el<HTMLSelectElement>("operation");
function log(message: string) {
  logs.push(`${new Date().toISOString()} ${message}`);
  logBox.textContent = logs.slice(-300).join("\n");
  logBox.scrollTop = logBox.scrollHeight;
}
function download(
  bytes: Uint8Array,
  name: string,
  type = "application/octet-stream",
) {
  const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
function refresh() {
  const destructive = ["install", "restore"].includes(operation.value);
  el("erase-field").hidden = !destructive;
  el("restore-field").hidden = operation.value !== "restore";
  el("description").textContent = destructive
    ? "This operation overwrites firmware and all settings. Download a backup first."
    : operation.value === "backup"
      ? "Read all 16 MiB of flash into a local .ctbackup file. This does not modify flash."
      : "Requires an existing compatible Coretastic partition layout. Firmware settings remain in their own partitions.";
  el<HTMLButtonElement>("connect").disabled =
    busy ||
    !!device ||
    !board.value ||
    !("serial" in navigator) ||
    !isSecureContext;
  el<HTMLButtonElement>("disconnect").disabled = busy || !device;
  el<HTMLButtonElement>("run").disabled =
    busy ||
    !device ||
    (!manifest && !["backup", "restore"].includes(operation.value)) ||
    (destructive && el<HTMLInputElement>("confirmation").value !== "ERASE");
  board.disabled = busy || !!device;
  operation.disabled = busy;
  el<HTMLInputElement>("backup-file").disabled = busy;
}
async function task(action: () => Promise<void>) {
  busy = true;
  refresh();
  try {
    await action();
  } catch (error) {
    status.textContent = `Failed: ${error instanceof Error ? error.message : String(error)}`;
    log(status.textContent);
  } finally {
    busy = false;
    refresh();
  }
}
el("connect").onclick = () =>
  task(async () => {
    status.textContent = "Connecting to ROM loader…";
    device = await UsbDevice.connect(log, (p) => {
      el<HTMLProgressElement>("progress").value = p;
    });
    status.textContent = `Connected: ${device.mac}`;
  });
el("disconnect").onclick = () =>
  task(async () => {
    await device?.disconnect();
    device = undefined;
    status.textContent = "Disconnected. Press RESET to boot.";
  });
el("run").onclick = () =>
  task(async () => {
    if (!device) throw new Error("Connect USB first.");
    status.textContent = "Working. Keep USB connected.";
    const op = operation.value;
    log(
      `Operation ${op}; board ${board.value}; release ${manifest?.version ?? "unavailable"}`,
    );
    if (op === "backup") {
      const bytes = await device.read(0, 0x1000000);
      download(
        backup(bytes, device.mac, board.value),
        `coretastic-${device.mac.replaceAll(":", "")}-${Date.now()}.ctbackup`,
      );
    } else if (op === "restore") {
      if (el<HTMLInputElement>("confirmation").value !== "ERASE")
        throw new Error("Type ERASE first.");
      const file = el<HTMLInputElement>("backup-file").files?.[0];
      if (!file) throw new Error("Select a .ctbackup file.");
      const bytes = restoreBackup(
        new Uint8Array(await file.arrayBuffer()),
        device.mac,
        board.value,
      );
      await device.restore(bytes);
    } else {
      if (!manifest) throw new Error("Release manifest unavailable.");
      if (
        op === "install" &&
        el<HTMLInputElement>("confirmation").value !== "ERASE"
      )
        throw new Error("Type ERASE first.");
      const assets = new Map<ImageName, Uint8Array>();
      for (const name of imageNames(op as Operation)) {
        const response = await fetch(
          new URL(`releases/${manifest.images[name].file}`, document.baseURI),
        );
        if (!response.ok)
          throw new Error(`Download ${name}: HTTP ${response.status}.`);
        assets.set(name, new Uint8Array(await response.arrayBuffer()));
      }
      await program(device, manifest, op as Operation, assets);
    }
    status.textContent =
      op === "backup"
        ? "Backup downloaded."
        : "Verified. Disconnect, then press RESET with PRG released.";
    log(status.textContent);
    el<HTMLInputElement>("confirmation").value = "";
  });
el("logs").onclick = () =>
  download(
    new TextEncoder().encode(logs.join("\n")),
    "coretastic-flash.log",
    "text/plain",
  );
board.onchange = refresh;
operation.onchange = () => {
  el<HTMLInputElement>("confirmation").value = "";
  refresh();
};
el("confirmation").oninput = refresh;
el("support").textContent =
  isSecureContext && "serial" in navigator
    ? "Web Serial is available. Hardware flashing requires a compatible USB device."
    : "This browser cannot use Web Serial here. Open HTTPS in desktop Chrome or Edge, or use the command-line flasher. Android browsers without Web Serial cannot flash directly.";
refresh();
void task(async () => {
  const response = await fetch(
    new URL("releases/manifest.json", document.baseURI),
  );
  if (!response.ok)
    throw new Error(
      `Release manifest unavailable (HTTP ${response.status}). Backups remain available.`,
    );
  manifest = validateManifest(await response.json());
  el("release").textContent =
    `Release ${manifest.version} · MeshCore ${manifest.upstream.meshcore.version} · Meshtastic ${manifest.upstream.meshtastic.version}`;
  log(el("release").textContent!);
});
