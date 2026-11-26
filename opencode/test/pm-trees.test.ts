import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  symlinkSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { startTimeOf, sweepTrees } from "../src/pm/trees.ts";
import type { Proc, TreeHost } from "../src/pm/trees.ts";

const roots: string[] = [];

// not `after`: a top-level hook may run between two awaited async tests
process.on("exit", () => {
  for (const root of roots) {
    rmSync(root, { recursive: true, force: true });
  }
});

interface Fake {
  /** The `/proc/<pid>/stat` text, by pid. */
  stat: Map<number, string>;
  exe: Map<number, string>;
  cmdline: Map<number, string[]>;
  /** The environ entries by pid; a pid not in the map has the PM's own (`hostOf` fills it in). */
  environ: Map<number, string[] | undefined>;
  killed: number[];
}

/** The time the fake host says it is; a record's age is counted from it. */
const NOW_MS = 1_700_000_000_000;

/** Makes a file `seconds` old as of `NOW_MS`. */
function setAge(path: string, seconds: number): void {
  const when = new Date(NOW_MS - seconds * 1000);
  utimesSync(path, when, when);
}

/** A `/proc/<pid>/stat` line: the comm may hold spaces and parentheses, field 22 is `start`. */
function statLine(
  pid: number,
  start: string,
  state = "S",
  comm = "(un share)",
): string {
  // fields 4 to 21 are numbers this test does not read; the start time is field 22
  const between = Array.from({ length: 18 }, () => "0").join(" ");
  return `${pid} ${comm} ${state} ${between} ${start} 0 0`;
}

const UNSHARE = [
  "unshare",
  "--user",
  "--kill-child",
  "--mount",
  "sh",
  "-c",
  "x",
];

/** A process table with `pid` running as the wrapper's outer unshare, started at `start`. */
function table(entries: [number, string][] = []): Fake {
  const fake: Fake = {
    stat: new Map(),
    exe: new Map(),
    cmdline: new Map(),
    environ: new Map(),
    killed: [],
  };
  for (const [pid, start] of entries) {
    fake.stat.set(pid, statLine(pid, start));
    fake.exe.set(pid, "/usr/bin/unshare");
    fake.cmdline.set(pid, UNSHARE);
  }
  return fake;
}

/** The host over `fake`, for the PM whose pid folder is `folder` (its shells carry it in their environ). */
function hostOf(fake: Fake, folder: string, dies = true): TreeHost {
  const proc: Proc = {
    stat: (pid) => fake.stat.get(pid),
    exe: (pid) => fake.exe.get(pid),
    cmdline: (pid) => fake.cmdline.get(pid),
    environ: (pid) =>
      fake.environ.has(pid)
        ? fake.environ.get(pid)
        : ["PATH=/bin", `COVEN_PM_PIDS=${folder}`],
  };
  return {
    proc,
    now: () => NOW_MS,
    kill: (pid) => {
      fake.killed.push(pid);
      if (dies) {
        fake.stat.delete(pid);
      }
    },
    wait: () => Promise.resolve(),
  };
}

/** The PM's pid folder, made the way the plugin makes it (0700), with one file per `[name, content]`. */
function folderWith(records: [string, string][]): string {
  const parent = mkdtempSync(join(tmpdir(), "coven-trees-"));
  roots.push(parent);
  const folder = join(parent, "ses_pm");
  mkdirSync(folder, { mode: 0o700 });
  for (const [name, content] of records) {
    writeFileSync(join(folder, name), content);
  }
  return folder;
}

await test("startTimeOf reads field 22 and survives spaces and parentheses in the comm", () => {
  assert.equal(startTimeOf(statLine(7, "123456")), "123456");
  assert.equal(startTimeOf(statLine(7, "99", "S", "(a) b (c d)")), "99");
  assert.equal(startTimeOf("garbage"), undefined);
});

await test("a record whose process matches is killed with SIGKILL, then removed with its folder", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 1, skipped: 0, running: 0 });
  assert.deepEqual(fake.killed, [4242]);
  assert.equal(existsSync(folder), false);
});

await test("a reused pid (another start time) is never killed; the stale record is removed and counted", async () => {
  const fake = table([[4242, "888"]]);
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
  assert.equal(existsSync(folder), false);
});

await test("a process whose exe is not an unshare is not killed and its record stays", async () => {
  const fake = table([[4242, "777"]]);
  fake.exe.set(4242, "/usr/bin/sleep");
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
  assert.deepEqual(readdirSync(folder), ["4242"]);
});

await test("an unshare whose binary was replaced while it runs (exe ends in ' (deleted)') is still killed", async () => {
  const fake = table([[4242, "777"]]);
  fake.exe.set(4242, "/usr/bin/unshare (deleted)");
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 1, skipped: 0, running: 0 });
  assert.deepEqual(fake.killed, [4242]);
});

await test("a binary that only looks like unshare with a suffix is not killed", async () => {
  const fake = table([[4242, "777"]]);
  fake.exe.set(4242, "/usr/bin/unshare-evil (deleted)");
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
});

await test("a record that is gone between the listing and the read (another stop took it) is pruned, not raised", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  // listed by readdir, but reading it fails with ENOENT: a link to nothing stands for the race
  symlinkSync(join(folder, "nowhere"), join(folder, "5000"));
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 1, skipped: 0, running: 0 });
  assert.deepEqual(fake.killed, [4242]);
  assert.equal(existsSync(folder), false);
});

await test("an exe that cannot be read is not killed either", async () => {
  const fake = table([[4242, "777"]]);
  fake.exe.delete(4242);
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
});

await test("an unshare whose command line has no --kill-child is not killed", async () => {
  const fake = table([[4242, "777"]]);
  fake.cmdline.set(4242, ["unshare", "--user", "--mount", "sh"]);
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
  assert.deepEqual(readdirSync(folder), ["4242"]);
});

await test("a gone process and a zombie are pruned, not killed and not counted", async () => {
  const fake = table([[5001, "10"]]);
  fake.stat.set(5001, statLine(5001, "10", "Z"));
  const folder = folderWith([
    ["5000", "9\n"],
    ["5001", "10\n"],
  ]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 0, running: 0 });
  assert.deepEqual(fake.killed, []);
  assert.equal(existsSync(folder), false);
});

await test("each record is judged alone and the folder stays while one record does", async () => {
  const fake = table([
    [1, "11"],
    [2, "22"],
    [3, "33"],
  ]);
  fake.exe.set(3, "/bin/sh");
  const folder = folderWith([
    ["1", "11\n"],
    ["2", "99\n"],
    ["3", "33\n"],
    ["4", "44\n"],
  ]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 1, skipped: 2, running: 0 });
  assert.deepEqual(fake.killed, [1]);
  assert.deepEqual(readdirSync(folder), ["3"]);
});

await test("a kill that does not end the process is counted as still running, not killed, and the record is kept", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder, false));
  assert.deepEqual(swept, { killed: 0, skipped: 0, running: 1 });
  assert.deepEqual(fake.killed, [4242]);
  assert.deepEqual(readdirSync(folder), ["4242"]);
});

await test("a kill that finds the process gone (ESRCH) is not counted and the record is removed", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  const host = hostOf(fake, folder);
  host.kill = () => {
    throw Object.assign(new Error("no such process"), { code: "ESRCH" });
  };
  const swept = await sweepTrees(folder, host);
  assert.deepEqual(swept, { killed: 0, skipped: 0, running: 0 });
  assert.equal(existsSync(folder), false);
});

await test("names that are not a pid are left alone; a folder that is not there sweeps nothing", async () => {
  const fake = table();
  const folder = folderWith([
    [".record.AbC123", "5\n"],
    ["notes", "x"],
  ]);
  assert.deepEqual(await sweepTrees(folder, hostOf(fake, folder)), {
    killed: 0,
    skipped: 0,
    running: 0,
  });
  assert.deepEqual(readdirSync(folder).sort(), [".record.AbC123", "notes"]);
  assert.deepEqual(
    await sweepTrees(join(folder, "nope", "nope"), hostOf(fake, folder)),
    { killed: 0, skipped: 0, running: 0 },
  );
});

await test("a leftover temp record older than 60 s is removed, so the folder goes; a fresh one stays", async () => {
  const fake = table();
  const folder = folderWith([
    [".record.OLD123", "5\n"],
    [".record.NEW456", "6\n"],
  ]);
  setAge(join(folder, ".record.OLD123"), 61);
  setAge(join(folder, ".record.NEW456"), 5);
  assert.deepEqual(await sweepTrees(folder, hostOf(fake, folder)), {
    killed: 0,
    skipped: 0,
    running: 0,
  });
  assert.deepEqual(readdirSync(folder), [".record.NEW456"]);
  setAge(join(folder, ".record.NEW456"), 61);
  await sweepTrees(folder, hostOf(fake, folder));
  assert.equal(existsSync(folder), false);
});

await test("another PM's unshare (same start time, exe and flags, another folder in its environ) is not killed and its record stays", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  fake.environ.set(4242, ["COVEN_PM_PIDS=/serve/isolate/pids/ses_other"]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
  assert.deepEqual(readdirSync(folder), ["4242"]);
});

await test("the folder must match as a whole entry: a longer path, a prefix, a different name and an unreadable environ are all refused", async () => {
  for (const environ of [
    (folder: string) => [`COVEN_PM_PIDS=${folder}2`],
    (folder: string) => [`COVEN_PM_PIDS=${folder.slice(0, -1)}`],
    (folder: string) => [`X_COVEN_PM_PIDS=${folder}`],
    (folder: string) => [`COVEN_PM_PIDS=${folder}\nX=1`],
    (folder: string) => [`COVEN_PM_PIDS= ${folder}`],
    () => [],
    () => undefined,
  ]) {
    const fake = table([[4242, "777"]]);
    const folder = folderWith([["4242", "777\n"]]);
    fake.environ.set(4242, environ(folder));
    const swept = await sweepTrees(folder, hostOf(fake, folder));
    assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
    assert.deepEqual(fake.killed, []);
    assert.deepEqual(readdirSync(folder), ["4242"]);
  }
});

await test("the right entry is found among others, wherever it sits in the environ", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "777\n"]]);
  fake.environ.set(4242, ["A=1", `COVEN_PM_PIDS=${folder}`, "B=2"]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 1, skipped: 0, running: 0 });
});

await test("a record whose content is not a start time counts as stale", async () => {
  const fake = table([[4242, "777"]]);
  const folder = folderWith([["4242", "not a number\n"]]);
  const swept = await sweepTrees(folder, hostOf(fake, folder));
  assert.deepEqual(swept, { killed: 0, skipped: 1, running: 0 });
  assert.deepEqual(fake.killed, []);
});
