import {
  chmodSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
} from "node:fs";
import { readdir, readFile, rm, rmdir, stat } from "node:fs/promises";
import { basename, join } from "node:path";

/**
 * Stops the isolation trees of a PM. The wrapper (`hooks/pm-shell-isolate.sh`) leaves one record
 * per shell in the PM's pid folder: a file named for the pid of the outer `unshare`, holding the
 * process start time. A stop kills the `unshare` of each record whose process is still the one
 * that wrote it; `--kill-child` then takes the whole namespace down, including a call that
 * OpenCode moved to the background.
 */

/** A session id that is safe as a folder name: no separator, no leading dot. */
const PLAIN_NAME = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/;
const PID_NAME = /^[0-9]+$/;
const UNSHARE = "unshare";
const KILL_CHILD_FLAG = "--kill-child";
/** Fields between the state (field 3) and the start time (field 22) of `/proc/<pid>/stat`. */
const START_AFTER_STATE = 18;
const GONE_CHECKS = 20;
const GONE_CHECK_MS = 50;
/** The wrapper's temp record (`mktemp .record.XXXXXX`) and how old one is left before it is cleared. */
const TEMP_RECORD = /^\.record\.[A-Za-z0-9]+$/;
const TEMP_RECORD_MAX_AGE_MS = 60_000;
const PIDS_ENV = "COVEN_PM_PIDS";

export function isPlainName(name: string): boolean {
  return PLAIN_NAME.test(name);
}

/**
 * Makes a PM's pid folder (0700) under `pidsDir`; throws when it cannot. A link or a file in the
 * place of either folder is an error, never followed.
 */
export function makePidFolder(pidsDir: string, folder: string): void {
  mkdirSync(pidsDir, { recursive: true, mode: 0o700 });
  try {
    mkdirSync(folder, { mode: 0o700 });
  } catch (error) {
    if (!isMissing(error, "EEXIST")) {
      throw error;
    }
  }
  for (const path of [pidsDir, folder]) {
    if (!lstatSync(path).isDirectory()) {
      throw new Error(`${path} is not a folder`);
    }
    chmodSync(path, 0o700);
  }
}

/** What `/proc/<pid>` says about a process; each is `undefined` when it cannot be read. */
export interface Proc {
  stat(pid: number): string | undefined;
  exe(pid: number): string | undefined;
  cmdline(pid: number): string[] | undefined;
  /** The `NAME=value` entries of the process's environment as it was when it started. */
  environ(pid: number): string[] | undefined;
}

export interface TreeHost {
  proc: Proc;
  /** SIGKILL; throws like `process.kill` (`ESRCH` when the process is gone). */
  kill(pid: number): void;
  wait(ms?: number): Promise<void>;
  /** Milliseconds since the epoch, to age a temp record. */
  now(): number;
}

export interface Swept {
  /** Isolation trees a SIGKILL ended: their pid was gone after the wait. */
  killed: number;
  /** Records whose pid is not, or is not any more, this PM's wrapper unshare. */
  skipped: number;
  /** Trees that still ran after the SIGKILL and the wait; their records stay. */
  running: number;
}

interface Stat {
  state: string;
  start: string;
}

/**
 * The state and start time of a `/proc/<pid>/stat` line. The command name (field 2) may hold
 * spaces and parentheses, so the fields are counted after the last closing parenthesis.
 */
function parseStat(stat: string): Stat | undefined {
  const closing = stat.lastIndexOf(")");
  if (closing < 0) {
    return undefined;
  }
  const fields = stat
    .slice(closing + 1)
    .trim()
    .split(/\s+/);
  const state = fields[0];
  const start = fields[START_AFTER_STATE + 1];
  return state && start && PID_NAME.test(start) ? { state, start } : undefined;
}

export function startTimeOf(stat: string): string | undefined {
  return parseStat(stat)?.start;
}

/** Whether the process is gone: not there, or a zombie that only waits to be reaped. */
function isGone(proc: Proc, pid: number): boolean {
  const stat = proc.stat(pid);
  return stat === undefined || parseStat(stat)?.state === "Z";
}

type Verdict = "gone" | "stale" | "ours" | "other";

/**
 * What a record's pid is now: gone, `stale` (another process has the pid), `ours` (the wrapper's
 * outer unshare of the PM whose pid folder is `folder`) or `other` (the same start time, but not
 * that: not an unshare with `--kill-child`, or one whose environment does not hold this PM's
 * folder as a whole entry, or one that cannot be read).
 */
function judge(
  proc: Proc,
  pid: number,
  recorded: string,
  folder: string,
): Verdict {
  const stat = proc.stat(pid);
  if (stat === undefined) {
    return "gone";
  }
  const found = parseStat(stat);
  if (found?.state === "Z") {
    return "gone";
  }
  if (found === undefined || found.start !== recorded) {
    return "stale";
  }
  return isPmUnshare(proc, pid, folder) ? "ours" : "other";
}

/** Whether the process is the outer `unshare` of the wrapper of the PM whose folder is `folder`. */
function isPmUnshare(proc: Proc, pid: number, folder: string): boolean {
  const exe = proc.exe(pid);
  return (
    exe !== undefined &&
    basename(exe.replace(DELETED_SUFFIX, "")) === UNSHARE &&
    proc.cmdline(pid)?.includes(KILL_CHILD_FLAG) === true &&
    proc.environ(pid)?.includes(`${PIDS_ENV}=${folder}`) === true
  );
}

/** What the kernel appends to the exe link of a process whose binary was replaced while it runs. */
const DELETED_SUFFIX = / \(deleted\)$/;

function isMissing(error: unknown, code: string): boolean {
  return (error as NodeJS.ErrnoException | undefined)?.code === code;
}

/** The start time a record holds; `undefined` when another stop took the record meanwhile. */
async function recordedStart(path: string): Promise<string | undefined> {
  try {
    return (await readFile(path, "utf8")).trim();
  } catch (error) {
    if (isMissing(error, "ENOENT")) {
      return undefined;
    }
    throw error;
  }
}

async function waitUntilGone(host: TreeHost, pid: number): Promise<boolean> {
  for (let check = 0; check < GONE_CHECKS; check += 1) {
    if (isGone(host.proc, pid)) {
      return true;
    }
    await host.wait(GONE_CHECK_MS);
  }
  return isGone(host.proc, pid);
}

/**
 * Kills the trees of the records in one PM's pid folder. A record is killed only when its pid
 * still has the recorded start time, runs an `unshare` binary, has `--kill-child` in its command
 * line and carries this PM's folder in its environment (`COVEN_PM_PIDS`): a reused pid, or another
 * PM's unshare, is never killed. Records of processes that are gone are removed, so are the
 * records of a reused pid (counted as skipped); a record that is not ours stays (counted as
 * skipped), and so does the record of a tree that still ran after the SIGKILL and the wait
 * (counted as running). A temp record the wrapper left over is removed once it is old. The folder
 * is removed when it ends empty.
 */
export async function sweepTrees(
  folder: string,
  host: TreeHost,
): Promise<Swept> {
  let names: string[];
  try {
    names = await readdir(folder);
  } catch (error) {
    if (isMissing(error, "ENOENT")) {
      return { killed: 0, skipped: 0, running: 0 };
    }
    throw error;
  }
  const swept: Swept = { killed: 0, skipped: 0, running: 0 };
  for (const name of names) {
    const path = join(folder, name);
    if (TEMP_RECORD.test(name)) {
      await removeIfOld(path, host.now());
    } else if (PID_NAME.test(name)) {
      await sweepRecord(path, Number(name), folder, host, swept);
    }
  }
  await removeIfEmpty(folder);
  return swept;
}

async function sweepRecord(
  path: string,
  pid: number,
  folder: string,
  host: TreeHost,
  swept: Swept,
): Promise<void> {
  const recorded = await recordedStart(path);
  if (recorded === undefined) {
    await rm(path, { force: true });
    return;
  }
  const verdict = judge(host.proc, pid, recorded, folder);
  if (verdict === "other") {
    swept.skipped += 1;
    return;
  }
  if (verdict === "stale") {
    swept.skipped += 1;
  }
  if (verdict === "ours") {
    const result = await killTree(host, pid);
    if (result === "alive") {
      swept.running += 1;
      return;
    }
    swept.killed += result === "gone" ? 1 : 0;
  }
  await rm(path, { force: true });
}

/** Removes a leftover temp record that is older than `TEMP_RECORD_MAX_AGE_MS`. */
async function removeIfOld(path: string, now: number): Promise<void> {
  try {
    if (now - (await stat(path)).mtimeMs > TEMP_RECORD_MAX_AGE_MS) {
      await rm(path, { force: true });
    }
  } catch (error) {
    if (!isMissing(error, "ENOENT")) {
      throw error;
    }
  }
}

/**
 * Sends SIGKILL and says what followed: `missing` (the process was gone before the signal),
 * `gone` (it went within the wait) or `alive` (it is still there; its record is kept).
 */
async function killTree(
  host: TreeHost,
  pid: number,
): Promise<"missing" | "gone" | "alive"> {
  try {
    host.kill(pid);
  } catch (error) {
    if (isMissing(error, "ESRCH")) {
      return "missing";
    }
    throw error;
  }
  return (await waitUntilGone(host, pid)) ? "gone" : "alive";
}

async function removeIfEmpty(folder: string): Promise<void> {
  try {
    await rmdir(folder);
  } catch (error) {
    if (!isMissing(error, "ENOTEMPTY") && !isMissing(error, "ENOENT")) {
      throw error;
    }
  }
}

function readOrUndefined<T>(read: () => T): T | undefined {
  try {
    return read();
  } catch {
    return undefined;
  }
}

/** The running system: `/proc` and `process.kill`. */
export function systemHost(): TreeHost {
  return {
    proc: {
      stat: (pid) =>
        readOrUndefined(() => readFileSync(`/proc/${pid}/stat`, "utf8")),
      exe: (pid) => readOrUndefined(() => readlinkSync(`/proc/${pid}/exe`)),
      environ: (pid) =>
        readOrUndefined(() =>
          readFileSync(`/proc/${pid}/environ`, "utf8")
            .split("\0")
            .filter((entry) => entry !== ""),
        ),
      cmdline: (pid) =>
        readOrUndefined(() =>
          readFileSync(`/proc/${pid}/cmdline`, "utf8")
            .split("\0")
            .filter((arg) => arg !== ""),
        ),
    },
    kill: (pid) => process.kill(pid, "SIGKILL"),
    now: () => Date.now(),
    wait: (ms = GONE_CHECK_MS) =>
      new Promise((resolve) => {
        setTimeout(resolve, ms);
      }),
  };
}

/** Stops the trees of the PM session `sessionID`, whose records are in `<pidsDir>/<sessionID>`. */
export function stopTreesIn(
  pidsDir: string,
  host: TreeHost = systemHost(),
): (sessionID: string) => Promise<Swept> {
  return (sessionID) =>
    isPlainName(sessionID)
      ? sweepTrees(join(pidsDir, sessionID), host)
      : Promise.resolve({ killed: 0, skipped: 0, running: 0 });
}
