import { createHash } from "node:crypto";
import { lstat, readdir, readFile, readlink } from "node:fs/promises";
import type { Stats } from "node:fs";
import { join } from "node:path";

const ABSENT = "absent";

async function describe(path: string, stats: Stats): Promise<string> {
  if (stats.isDirectory()) {
    return "dir";
  }
  if (stats.isSymbolicLink()) {
    return `link ${await readlink(path)}`;
  }
  const hash = createHash("sha256")
    .update(await readFile(path))
    .digest("hex");
  return `${stats.size} ${stats.mtimeMs} ${hash}`;
}

async function stat(path: string): Promise<Stats | undefined> {
  try {
    return await lstat(path);
  } catch {
    return undefined;
  }
}

async function visit(path: string, seen: Map<string, string>): Promise<void> {
  const stats = await stat(path);
  if (stats === undefined) {
    seen.set(path, ABSENT);
    return;
  }
  seen.set(path, await describe(path, stats));
  if (stats.isDirectory()) {
    for (const name of await readdir(path)) {
      await visit(join(path, name), seen);
    }
  }
}

/**
 * A record of every entry under each root: `dir`, `<size> <mtimeMs> <sha256>` for a file,
 * `link <target>` for a symlink, or `absent` for a root that does not exist.
 */
export async function snapshot(
  roots: readonly string[],
): Promise<Map<string, string>> {
  const seen = new Map<string, string>();
  for (const root of roots) {
    await visit(root, seen);
  }
  return seen;
}

/** One line per path that was added, removed or changed between two snapshots. */
export function snapshotDiff(
  before: ReadonlyMap<string, string>,
  after: ReadonlyMap<string, string>,
): string[] {
  const lines: string[] = [];
  for (const [path, was] of before) {
    const now = after.get(path);
    if (now === undefined) {
      lines.push(`removed: ${path}`);
    } else if (now !== was) {
      lines.push(`changed: ${path} (${was} -> ${now})`);
    }
  }
  for (const path of after.keys()) {
    if (!before.has(path)) {
      lines.push(`added: ${path}`);
    }
  }
  return lines.sort();
}
