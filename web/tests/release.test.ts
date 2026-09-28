import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import {
  APPS,
  SECTOR,
  partitionBinary,
  plannedImages,
  recordOffset,
  validateAsset,
  validateManifest,
} from "../src/contract";
import { program } from "../src/flasher";

it("accepts every actual packaged image and preflights every supported write operation", async () => {
  const directory = resolve(import.meta.dirname, "../../release");
  const manifest = validateManifest(
    JSON.parse(readFileSync(resolve(directory, "manifest.json"), "utf8")),
  );
  const assets = new Map<string, Uint8Array>();
  const load = (file: string) =>
    new Uint8Array(readFileSync(resolve(directory, file)));
  for (const { image, kind } of plannedImages(manifest, "install")) {
    validateAsset(image, kind, load(image.file));
    assets.set(image.file, load(image.file));
  }
  for (const app of APPS)
    for (const image of manifest.apps[app]) {
      validateAsset(image, "app", load(image.file));
      assets.set(image.file, load(image.file));
    }
  const oldest = Object.fromEntries(
    APPS.map((app) => [app, manifest.apps[app].at(-1)!.version]),
  );
  for (const versions of [{}, oldest])
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
            address === 0
              ? assets.get("bootloader.bin")!
              : APPS.some((app) => recordOffset(app) === address)
                ? new Uint8Array(SECTOR).fill(255)
                : partitionBinary(),
          write: async (files, erase) => {
            expect(erase).toBe(operation === "install");
            expect(files.at(-1)?.address).toBe(0xe000);
            count++;
          },
        },
        manifest,
        operation,
        assets,
        versions,
        true,
      );
      expect(count).toBe(1);
    }
});
