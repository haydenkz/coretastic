import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import {
  APPS,
  SECTOR,
  board,
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
  const load = (file: string) =>
    new Uint8Array(readFileSync(resolve(directory, file)));
  for (const [id, release] of Object.entries(manifest.boards)) {
    const size = board(id).flash_size;
    const assets = new Map<string, Uint8Array>();
    for (const { image, kind } of plannedImages(release, "install")) {
      validateAsset(image, kind, load(image.file), size);
      assets.set(image.file, load(image.file));
    }
    for (const app of APPS)
      for (const image of release.apps[app]) {
        validateAsset(image, "app", load(image.file), size);
        assets.set(image.file, load(image.file));
      }
    const oldest = Object.fromEntries(
      APPS.map((app) => [app, release.apps[app].at(-1)!.version]),
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
                ? assets.get(release.images.bootloader.file)!
                : APPS.some((app) => recordOffset(id, app) === address)
                  ? new Uint8Array(SECTOR).fill(255)
                  : partitionBinary(id),
            write: async (files, erase) => {
              expect(erase).toBe(operation === "install");
              expect(files.at(-1)?.address).toBe(0xe000);
              count++;
            },
          },
          manifest,
          id,
          operation,
          assets,
          versions,
          true,
        );
        expect(count, `${id} ${operation}`).toBe(1);
      }
  }
});
