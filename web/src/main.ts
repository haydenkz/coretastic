import "./style.css";
import {
  BOARD,
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
  logBox = el("log"),
  hint = el("hint"),
  progressField = el("progress-field"),
  progress = el<HTMLProgressElement>("progress"),
  progressText = el("progress-text");
const logs: string[] = [];
let device: UsbDevice | undefined,
  manifest: Manifest | undefined,
  busy = false,
  selectedOperation = "install";
const board = el<HTMLInputElement>("board"),
  updateTargets = [
    ...document.querySelectorAll<HTMLInputElement>(
      'input[name="update-target"]',
    ),
  ],
  backupActions = [
    ...document.querySelectorAll<HTMLInputElement>(
      'input[name="backup-action"]',
    ),
  ];
const modes = [
  ...document.querySelectorAll<HTMLInputElement>('input[name="mode"]'),
];
const operationUi: Record<string, { description: string; button: string }> = {
  meshcore: {
    description: "Keeps existing MeshCore settings.",
    button: "Update MeshCore",
  },
  meshtastic: {
    description: "Keeps existing Meshtastic settings.",
    button: "Update Meshtastic",
  },
  selector: {
    description: "Keeps both apps and their settings.",
    button: "Update boot menu",
  },
  recovery: {
    description: "Repairs boot files and keeps app settings.",
    button: "Repair boot files",
  },
  install: {
    description: "Installs both apps and the boot menu. Erases all settings.",
    button: "Install Coretastic",
  },
  backup: {
    description: "Downloads a private full-device backup.",
    button: "Back up device",
  },
  restore: {
    description: "Erases current data, then restores the backup.",
    button: "Restore backup",
  },
};
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
type StatusKind = "info" | "working" | "success" | "error";
function setStatus(message: string, kind: StatusKind = "info") {
  status.textContent = message;
  status.className = `mt-2 text-sm ${kind === "info" ? "" : kind}`;
}
function setProgress(percent: number) {
  const clamped = Math.max(0, Math.min(100, percent));
  progress.value = clamped;
  progressText.textContent = `${Math.round(clamped)}%`;
  progressField.hidden = clamped === 0;
}
function refresh() {
  const mode = modes.find((control) => control.checked)!.value;
  const op =
    mode === "install"
      ? "install"
      : mode === "update"
        ? updateTargets.find((control) => control.checked)!.value
        : backupActions.find((control) => control.checked)!.value;
  selectedOperation = op;
  el("update-field").hidden = mode !== "update";
  el("backup-action-field").hidden = mode !== "backup";
  el("install-field").hidden = mode !== "install";
  const destructive = ["install", "restore"].includes(op);
  const restore = op === "restore";
  const restoreFile = el<HTMLInputElement>("backup-file").files?.[0];
  el("erase-field").hidden = !destructive;
  el("restore-field").hidden = !restore;
  const ui = operationUi[op];
  el("description").textContent = ui.description;
  el<HTMLButtonElement>("run").textContent = ui.button;
  el("operation-title").textContent = ui.button;
  const noSerial = !("serial" in navigator) || !isSecureContext;
  el<HTMLButtonElement>("connect").disabled =
    busy || !!device || !board.checked || noSerial;
  el<HTMLButtonElement>("disconnect").disabled = busy || !device;
  const needsManifest = !["backup", "restore"].includes(op);
  el<HTMLButtonElement>("run").disabled =
    busy ||
    !device ||
    (needsManifest && !manifest) ||
    (destructive && !el<HTMLInputElement>("confirmation").checked) ||
    (restore && !restoreFile);
  board.disabled = busy || !!device;
  for (const control of modes) control.disabled = busy;
  for (const control of updateTargets) control.disabled = busy;
  for (const control of backupActions) control.disabled = busy;
  el<HTMLInputElement>("backup-file").disabled = busy;
  hint.textContent =
    !busy && device && needsManifest && !manifest
      ? "Waiting for the release manifest."
      : !busy && device && restore && !restoreFile
        ? "Select a .ctbackup file."
        : !busy &&
            device &&
            destructive &&
            !el<HTMLInputElement>("confirmation").checked
          ? "Confirm that existing settings can be erased."
          : "";
  hint.hidden = !hint.textContent;
}
async function task(action: () => Promise<void>) {
  busy = true;
  refresh();
  try {
    await action();
  } catch (error) {
    const message = `Failed: ${error instanceof Error ? error.message : String(error)}`;
    setStatus(message, "error");
    log(message);
  } finally {
    busy = false;
    refresh();
  }
}
el("connect").onclick = () =>
  task(async () => {
    setProgress(0);
    setStatus("Connecting to ROM loader…", "working");
    device = await UsbDevice.connect(log, setProgress, () => {
      device = undefined;
      const message = "USB device disconnected. Reconnect before retrying.";
      setStatus(message, "error");
      log(message);
      refresh();
    });
    setStatus(`Connected: ${device.mac}`, "success");
  });
el("disconnect").onclick = () =>
  task(async () => {
    await device?.disconnect();
    device = undefined;
    setStatus("Disconnected. Press RESET to boot.");
  });
el("run").onclick = () =>
  task(async () => {
    if (!device) throw new Error("Connect USB first.");
    setProgress(0);
    setStatus("Working. Keep USB connected.", "working");
    const op = selectedOperation;
    log(
      `Operation ${op}; board ${BOARD}; release ${manifest?.version ?? "unavailable"}`,
    );
    if (op === "backup") {
      const bytes = await device.read(0, 0x1000000);
      download(
        backup(bytes, device.mac, BOARD),
        `coretastic-${device.mac.replaceAll(":", "")}-${Date.now()}.ctbackup`,
      );
    } else if (op === "restore") {
      if (!el<HTMLInputElement>("confirmation").checked)
        throw new Error("Confirm erase first.");
      const file = el<HTMLInputElement>("backup-file").files?.[0];
      if (!file) throw new Error("Select a .ctbackup file.");
      const bytes = restoreBackup(
        new Uint8Array(await file.arrayBuffer()),
        device.mac,
        BOARD,
      );
      await device.restore(bytes);
    } else {
      if (!manifest) throw new Error("Release manifest unavailable.");
      if (op === "install" && !el<HTMLInputElement>("confirmation").checked)
        throw new Error("Confirm erase first.");
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
    const done =
      op === "backup"
        ? "Backup downloaded."
        : "Verified. Disconnect, then press RESET with PRG released.";
    setStatus(done, "success");
    log(done);
    el<HTMLInputElement>("confirmation").checked = false;
  });
el("logs").onclick = () =>
  download(
    new TextEncoder().encode(logs.join("\n")),
    "coretastic-flash.log",
    "text/plain",
  );
board.onchange = refresh;
function changeOperation() {
  el<HTMLInputElement>("confirmation").checked = false;
  setProgress(0);
  refresh();
}
for (const control of modes) control.onchange = changeOperation;
for (const control of updateTargets) control.onchange = changeOperation;
for (const control of backupActions) control.onchange = changeOperation;
el("confirmation").onchange = refresh;
el("backup-file").onchange = refresh;
const serialAvailable = isSecureContext && "serial" in navigator;
if (!serialAvailable) {
  const support = el("support");
  support.textContent =
    "Web Serial is unavailable. Open this page over HTTPS in desktop Chrome or Edge, or use the command-line flasher.";
  support.className =
    "mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300";
  support.hidden = false;
}
el("theme").onclick = () => {
  const dark = document.documentElement.classList.toggle("dark");
  try {
    localStorage.setItem("coretastic-theme", dark ? "dark" : "light");
  } catch {}
};
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
