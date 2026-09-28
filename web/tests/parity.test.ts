import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { partitionBinary } from "../src/contract";

// The browser and Python flashers each build the partition table they compare
// against the device; they must agree byte for byte.
it("builds the same partition table as the Python flasher", () => {
  const python = execFileSync(
    process.env.PYTHON ?? "python3",
    [
      "-c",
      "from layout import partition_binary; print(partition_binary().hex())",
    ],
    {
      cwd: resolve(import.meta.dirname, "../../scripts/device"),
      encoding: "utf8",
    },
  ).trim();
  expect(Buffer.from(partitionBinary()).toString("hex")).toBe(python);
});
