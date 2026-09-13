import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import {
  partitionBinary,
  validateAsset,
  validateManifest,
} from "../src/contract";
import type { ImageName } from "../src/contract";
import { program } from "../src/flasher";

it("accepts every actual packaged image and preflights every supported write operation", async () => {
  const directory = resolve(import.meta.dirname, "../../release");
  const manifest = validateManifest(
    JSON.parse(readFileSync(resolve(directory, "manifest.json"), "utf8")),
  );
  const assets = new Map<ImageName, Uint8Array>();
  for (const name of Object.keys(manifest.images) as ImageName[]) {
    const bytes = new Uint8Array(
      readFileSync(resolve(directory, manifest.images[name].file)),
    );
    validateAsset(manifest, name, bytes);
    assets.set(name, bytes);
  }
  for (const operation of [
    "install",
    "recovery",
    "selector",
    "meshcore",
    "meshtastic",
  ] as const) {
    let count = 0;
    await program(
      {
        mac: "test",
        read: async (address) =>
          address === 0 ? assets.get("bootloader")! : partitionBinary(),
        write: async (files, erase) => {
          expect(erase).toBe(operation === "install");
          expect(files.at(-1)?.address).toBe(0xe000);
          count++;
        },
      },
      manifest,
      operation,
      assets,
    );
    expect(count).toBe(1);
  }
});
