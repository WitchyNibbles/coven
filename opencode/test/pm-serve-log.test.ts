import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { serveLogger } from "../src/pm/setup.ts";

const NOW = Date.UTC(2026, 10, 25, 12, 0, 0);

await test("a line is appended to the serve folder's log with a time stamp", () => {
  const folder = mkdtempSync(join(tmpdir(), "coven-serve-log-"));
  try {
    const own: string[] = [];
    const write = serveLogger(folder, {
      now: () => NOW,
      log: (l) => own.push(l),
    });
    write("first");
    write("second");
    assert.equal(
      readFileSync(join(folder, "log"), "utf8"),
      "2026-11-25T12:00:00.000Z first\n2026-11-25T12:00:00.000Z second\n",
    );
    assert.deepEqual(own, []);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});

await test("a line that cannot be written goes to the server log instead", () => {
  const folder = join(tmpdir(), "coven-serve-log-missing", "nowhere");
  const own: string[] = [];
  serveLogger(folder, { now: () => NOW, log: (l) => own.push(l) })("lost");
  assert.equal(own.length, 1);
  assert.match(
    own[0] ?? "",
    /^2026-11-25T12:00:00.000Z lost \(serve log unwritable: /,
  );
});
