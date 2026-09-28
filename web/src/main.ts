import "./style.css";
import {
  APPS,
  BOARDS,
  backup,
  board as boardProfile,
  plannedImages,
  restoreBackup,
  settingsEraseRequired,
  validateManifest,
} from "./contract";
import type {
  App,
  BoardRelease,
  Manifest,
  Operation,
  Versions,
} from "./contract";
import { UsbDevice, inspect, program } from "./flasher";
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
// Versions read from the connected device; empty when unknown.
let installed: Partial<Record<App, string | null>> = {};
const APP_NAMES: Record<App, string> = {
  meshcore: "MeshCore",
  meshtastic: "Meshtastic",
};
const versionSelect = (app: App) => el<HTMLSelectElement>(`version-${app}`);
const eraseSettings = el<HTMLInputElement>("erase-settings");
function selectedVersions(): Versions {
  const versions: Versions = {};
  for (const app of APPS)
    if (versionSelect(app).value) versions[app] = versionSelect(app).value;
  return versions;
}
const boardSelect = el<HTMLSelectElement>("board-select"),
  showExperimental = el<HTMLInputElement>("show-experimental");
const selectedBoard = () => boardSelect.value;
const selectedRelease = (): BoardRelease | undefined =>
  manifest?.boards[selectedBoard()];
// Boards this release ships (or every known board before it loads), with
// experimental ones only on request.
function populateBoards() {
  const previous = selectedBoard();
  const offered = Object.values(BOARDS).filter(
    (profile) =>
      (!manifest || profile.id in manifest.boards) &&
      (!profile.experimental ||
        showExperimental.checked ||
        profile.id === previous),
  );
  boardSelect.replaceChildren(
    ...offered.map(
      (profile) =>
        new Option(
          profile.experimental
            ? `${profile.name} (experimental)`
            : profile.name,
          profile.id,
        ),
    ),
  );
  if (offered.some((profile) => profile.id === previous))
    boardSelect.value = previous;
  populateVersions();
}
function populateVersions() {
  for (const app of APPS) {
    const select = versionSelect(app);
    select.replaceChildren(
      ...(selectedRelease()?.apps[app] ?? []).map(
        ({ version }) => new Option(version, version),
      ),
    );
  }
}
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
    description:
      "Keeps existing MeshCore settings unless you install an older version.",
    button: "Update MeshCore",
  },
  meshtastic: {
    description:
      "Keeps existing Meshtastic settings unless you install an older version.",
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
  const apps = APPS.filter((app) => op === "install" || op === app);
  el("versions-field").hidden = apps.length === 0 || !selectedRelease();
  for (const app of APPS) {
    el(`version-${app}-field`).hidden = !apps.includes(app);
    versionSelect(app).disabled = busy;
    for (const [index, option] of [...versionSelect(app).options].entries())
      option.text = [
        option.value,
        index === 0 ? "newest" : "",
        installed[app] === option.value ? "installed" : "",
      ]
        .filter(Boolean)
        .join(" · ");
  }
  const inspected = device && Object.keys(installed).length > 0;
  el("installed").hidden = !inspected;
  el("installed").textContent = `Installed: ${APPS.map(
    (app) => `${APP_NAMES[app]} ${installed[app] ?? "unidentified"}`,
  ).join(" · ")}`;
  // Only an app update can keep or erase that app's settings.
  const updated = apps.length === 1 && op !== "install" ? apps[0] : undefined;
  el("settings-field").hidden = !updated;
  const eraseRequired =
    !!updated &&
    !!inspected &&
    settingsEraseRequired(
      installed[updated] ?? null,
      versionSelect(updated).value,
    );
  eraseSettings.disabled = busy;
  if (updated)
    el("erase-settings-label").textContent = eraseRequired
      ? `Erase ${APP_NAMES[updated]} settings (required: older than the installed ${installed[updated] ?? "unidentified version"})`
      : `Erase ${APP_NAMES[updated]} settings`;
  const restoreFile = el<HTMLInputElement>("backup-file").files?.[0];
  el("erase-field").hidden = !destructive;
  el("restore-field").hidden = !restore;
  const ui = operationUi[op];
  el("description").textContent = ui.description;
  el<HTMLButtonElement>("run").textContent = ui.button;
  el("operation-title").textContent = ui.button;
  const noSerial = !("serial" in navigator) || !isSecureContext;
  el<HTMLButtonElement>("connect").disabled =
    busy || !!device || !board.checked || !selectedBoard() || noSerial;
  el<HTMLButtonElement>("disconnect").disabled = busy || !device;
  const needsManifest = !["backup", "restore"].includes(op);
  el<HTMLButtonElement>("run").disabled =
    busy ||
    !device ||
    (needsManifest && !selectedRelease()) ||
    (destructive && !el<HTMLInputElement>("confirmation").checked) ||
    (eraseRequired && !eraseSettings.checked) ||
    (restore && !restoreFile);
  board.disabled = busy || !!device;
  boardSelect.disabled = showExperimental.disabled = busy || !!device;
  el("board-warning").hidden = !BOARDS[selectedBoard()]?.experimental;
  for (const control of modes) control.disabled = busy;
  for (const control of updateTargets) control.disabled = busy;
  for (const control of backupActions) control.disabled = busy;
  el<HTMLInputElement>("backup-file").disabled = busy;
  hint.textContent =
    !busy && device && needsManifest && !selectedRelease()
      ? manifest
        ? "This release has no images for the selected board."
        : "Waiting for the release manifest."
      : !busy && device && restore && !restoreFile
        ? "Select a .ctbackup file."
        : !busy &&
            device &&
            destructive &&
            !el<HTMLInputElement>("confirmation").checked
          ? "Confirm that existing settings can be erased."
          : !busy && eraseRequired && !eraseSettings.checked
            ? "Installing an older version erases that app's settings. Back up first, then confirm."
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
    device = await UsbDevice.connect(selectedBoard(), log, setProgress, () => {
      device = undefined;
      installed = {};
      const message = "USB device disconnected. Reconnect before retrying.";
      setStatus(message, "error");
      log(message);
      refresh();
    });
    installed = (await inspect(device, device.board)).installed;
    setStatus(`Connected: ${device.mac}`, "success");
  });
el("disconnect").onclick = () =>
  task(async () => {
    await device?.disconnect();
    device = undefined;
    installed = {};
    setStatus("Disconnected. Press RESET to boot.");
  });
el("run").onclick = () =>
  task(async () => {
    if (!device) throw new Error("Connect USB first.");
    setProgress(0);
    setStatus("Working. Keep USB connected.", "working");
    const op = selectedOperation;
    const versions = selectedVersions();
    // The board is fixed while connected, so the device's board is authoritative.
    const boardId = device.board;
    log(
      `Operation ${op}; board ${boardId}; release ${manifest?.version ?? "unavailable"}; ${APPS.map((app) => `${app} ${versions[app] ?? "-"}`).join(", ")}`,
    );
    if (op === "backup") {
      const bytes = await device.read(0, boardProfile(boardId).flash_size);
      download(
        backup(bytes, device.mac, boardId),
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
        boardId,
      );
      await device.restore(bytes);
    } else {
      const release = selectedRelease();
      if (!manifest || !release)
        throw new Error("This release has no images for the selected board.");
      if (op === "install" && !el<HTMLInputElement>("confirmation").checked)
        throw new Error("Confirm erase first.");
      const assets = new Map<string, Uint8Array>();
      for (const { image } of plannedImages(
        release,
        op as Operation,
        versions,
      )) {
        const response = await fetch(
          new URL(`releases/${image.file}`, document.baseURI),
          // A stale cached image would fail its checksum on every retry.
          { cache: "no-cache" },
        );
        if (!response.ok)
          throw new Error(`Download ${image.file}: HTTP ${response.status}.`);
        assets.set(image.file, new Uint8Array(await response.arrayBuffer()));
      }
      await program(
        device,
        manifest,
        boardId,
        op as Operation,
        assets,
        versions,
        eraseSettings.checked,
      );
    }
    // The device may have disconnected; otherwise show what is now installed.
    if (device) installed = (await inspect(device, device.board)).installed;
    const done =
      op === "backup"
        ? "Backup downloaded."
        : "Verified. Disconnect, then press RESET with PRG released.";
    setStatus(done, "success");
    log(done);
    el<HTMLInputElement>("confirmation").checked = false;
    eraseSettings.checked = false;
  });
el("logs").onclick = () =>
  download(
    new TextEncoder().encode(logs.join("\n")),
    "coretastic-flash.log",
    "text/plain",
  );
board.onchange = refresh;
boardSelect.onchange = () => {
  board.checked = false;
  populateVersions();
  changeOperation();
};
showExperimental.onchange = () => {
  populateBoards();
  refresh();
};
function changeOperation() {
  el<HTMLInputElement>("confirmation").checked = false;
  eraseSettings.checked = false;
  setProgress(0);
  refresh();
}
for (const control of modes) control.onchange = changeOperation;
for (const control of updateTargets) control.onchange = changeOperation;
for (const control of backupActions) control.onchange = changeOperation;
el("confirmation").onchange = refresh;
eraseSettings.onchange = refresh;
for (const app of APPS) versionSelect(app).onchange = changeOperation;
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
populateBoards();
refresh();
void task(async () => {
  const response = await fetch(
    new URL("releases/manifest.json", document.baseURI),
    { cache: "no-cache" },
  );
  if (!response.ok)
    throw new Error(
      `Release manifest unavailable (HTTP ${response.status}). Backups remain available.`,
    );
  manifest = validateManifest(await response.json());
  populateBoards();
  const count = Object.keys(manifest.boards).length;
  el("release").textContent =
    `Release ${manifest.version} · ${count} board${count === 1 ? "" : "s"}`;
  log(el("release").textContent!);
});
