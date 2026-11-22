import assert from "node:assert/strict";
import {
  mkdir,
  mkdtemp,
  rm,
  unlink,
  utimes,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { snapshot, snapshotDiff } from "./harness/snapshot.ts";

async function withTree(body: (root: string) => Promise<void>): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "coven-snap-"));
  try {
    await mkdir(join(root, "sub"));
    await writeFile(join(root, "a.txt"), "alpha");
    await writeFile(join(root, "sub", "b.txt"), "beta");
    await body(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

await test("an unchanged tree has an empty diff", () =>
  withTree(async (root) => {
    const before = await snapshot([root]);
    assert.deepEqual(snapshotDiff(before, await snapshot([root])), []);
  }));

await test("every entry under the root is recorded", () =>
  withTree(async (root) => {
    const seen = await snapshot([root]);
    assert.equal(seen.get(root), "dir");
    assert.equal(seen.get(join(root, "sub")), "dir");
    assert.match(
      seen.get(join(root, "a.txt")) ?? "",
      /^5 \d+(\.\d+)? [0-9a-f]{64}$/,
    );
    assert.ok(seen.has(join(root, "sub", "b.txt")));
  }));

await test("changed bytes of the same size name the file", () =>
  withTree(async (root) => {
    const before = await snapshot([root]);
    const then = new Date(
      Number((before.get(join(root, "a.txt")) ?? "").split(" ")[1]),
    );
    await writeFile(join(root, "a.txt"), "omega");
    await utimes(join(root, "a.txt"), then, then);
    const diff = snapshotDiff(before, await snapshot([root]));
    assert.equal(diff.length, 1);
    assert.match(diff[0] ?? "", /a\.txt/);
  }));

await test("a changed mtime names the file", () =>
  withTree(async (root) => {
    const before = await snapshot([root]);
    const later = new Date(Date.now() + 60_000);
    await utimes(join(root, "sub", "b.txt"), later, later);
    const diff = snapshotDiff(before, await snapshot([root]));
    assert.equal(diff.length, 1);
    assert.match(diff[0] ?? "", /b\.txt/);
  }));

await test("an added file names the file", () =>
  withTree(async (root) => {
    const before = await snapshot([root]);
    await writeFile(join(root, "sub", "new.txt"), "x");
    const diff = snapshotDiff(before, await snapshot([root]));
    assert.equal(diff.length, 1);
    assert.match(diff[0] ?? "", /new\.txt/);
  }));

await test("a deleted file names the file", () =>
  withTree(async (root) => {
    const before = await snapshot([root]);
    await unlink(join(root, "a.txt"));
    const diff = snapshotDiff(before, await snapshot([root]));
    assert.equal(diff.length, 1);
    assert.match(diff[0] ?? "", /a\.txt/);
  }));

await test("a missing root records absent, and its appearance is a change", () =>
  withTree(async (root) => {
    const missing = join(root, "nope");
    const before = await snapshot([missing]);
    assert.equal(before.get(missing), "absent");
    await mkdir(missing);
    const diff = snapshotDiff(before, await snapshot([missing]));
    assert.equal(diff.length, 1);
    assert.match(diff[0] ?? "", /nope/);
  }));
