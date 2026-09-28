import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { BOARDS, partitionBinary } from "../src/contract";

// The browser and Python flashers each build the partition table they compare
// against the device; they must agree byte for byte for every board.
it("builds the same partition tables as the Python flasher", () => {
  const python = JSON.parse(
    execFileSync(
      process.env.PYTHON ?? "python3",
      [
        "-c",
        "import json; from layout import boards, partition_binary; " +
          "print(json.dumps({b: partition_binary(b).hex() for b in boards()}))",
      ],
      {
        cwd: resolve(import.meta.dirname, "../../scripts/device"),
        encoding: "utf8",
      },
    ),
  );
  expect(Object.keys(python)).toEqual(Object.keys(BOARDS));
  for (const id of Object.keys(BOARDS))
    expect(Buffer.from(partitionBinary(id)).toString("hex")).toBe(python[id]);
});
