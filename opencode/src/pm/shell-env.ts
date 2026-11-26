import {
  accessSync,
  chmodSync,
  constants,
  lstatSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join } from "node:path";

import type { ExecuteBeforeEvent, ShellCreateEvent } from "../context.ts";
import { isPlainObject, messageOf } from "../join/fields.ts";
import type { CovenOptions } from "../options.ts";
import { givenWorkdir } from "../workdir.ts";
import { ROLE_ENV } from "./role.ts";
import type { Role, RoleLookup } from "./role.ts";

/** How long a recorded call waits for its shell to start. */
const EXPIRY_MS = 30_000;
const SERVER_PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD";
const PROFILE_PASSWORD_ENV = "OPENCODE_PASSWORD";
/** The wrapper's test seams (see `hooks/pm-shell-isolate.sh`); no shell may carry them. */
const WRAPPER_SEAMS = ["COVEN_ISOLATE_SYS", "COVEN_ISOLATE_PATH"];
const REAL_SHELL_ENV = "COVEN_PM_REAL_SHELL";
const HIDE_ENV = "COVEN_PM_HIDE";
const WRAPPER_NAME = "pm-shell-isolate.sh";
/** The folder of the serve folder that holds the wrapper's copy. */
const COPY_DIR = "isolate";
const SECRET_NAME = /^OPENCODE(_.*)?_(PASSWORD|TOKEN|KEY)$/;
const SHELL_TOOLS: ReadonlySet<string> = new Set(["shell", "bash"]);
/** The role a shell gets when its recorded calls disagree: the strictest. */
const MIXED: Role = "pm";

export interface ShellCall {
  role: Role | undefined;
  command: string;
  cwd: string;
  /** A second `cwd` string the call's shell may start under: an empty `workdir` has two. */
  alsoCwd?: string;
}

/** Shared by the entries one call leaves under its keys: taken under one, spent under all. */
interface Spend {
  spent: boolean;
}

interface Entry {
  role: Role | undefined;
  at: number;
  spend: Spend;
}

/**
 * The shell calls `execute.before` has seen and `create.before` has not started yet. The second
 * hook carries no session id, so a shell is paired to its call by what both hooks share: the
 * command and the `cwd` string as given.
 */
export class ShellQueue {
  private readonly entries = new Map<string, Entry[]>();

  record(call: ShellCall, now: number): void {
    const spend: Spend = { spent: false };
    for (const cwd of new Set([call.cwd, call.alsoCwd ?? call.cwd])) {
      const key = keyOf(call.command, cwd);
      const live = this.live(key, now);
      live.push({ role: call.role, at: now, spend });
      this.entries.set(key, live);
    }
  }

  /**
   * The role of the shell that is starting. When the live calls under the key agree, the oldest
   * is taken off the queue. When they differ in role the shell gets a PM, and the oldest call
   * that is not a PM's is taken off, so every take while a PM's call waits still returns a PM.
   * `undefined` when no call is waiting or the call had no role.
   */
  take(command: string, cwd: string, now: number): Role | undefined {
    const key = keyOf(command, cwd);
    const live = this.live(key, now);
    const mixed = new Set(live.map((entry) => entry.role)).size > 1;
    const taken = mixed ? takeNonPm(live) : live.shift();
    if (taken !== undefined) {
      taken.spend.spent = true;
    }
    if (live.length === 0) {
      this.entries.delete(key);
    } else {
      this.entries.set(key, live);
    }
    return mixed ? MIXED : taken?.role;
  }

  private live(key: string, now: number): Entry[] {
    return (this.entries.get(key) ?? []).filter(
      (entry) => !entry.spend.spent && now - entry.at < EXPIRY_MS,
    );
  }
}

/** Removes and returns the oldest entry whose role is not `pm`. */
function takeNonPm(live: Entry[]): Entry | undefined {
  const index = live.findIndex((entry) => entry.role !== MIXED);
  return live.splice(index, 1)[0];
}

function keyOf(command: string, cwd: string): string {
  return `${command}\0${cwd}`;
}

/**
 * Deletes what a model's shell must not see: the server password, the provider key and the
 * isolation wrapper's two test seams (a PM that could set them would steer the wrapper).
 */
export function scrubEnv(
  env: Record<string, string | undefined>,
  keyEnv: string,
): void {
  const secrets = [
    SERVER_PASSWORD_ENV,
    PROFILE_PASSWORD_ENV,
    keyEnv,
    ...WRAPPER_SEAMS,
  ];
  for (const name of Object.keys(env)) {
    if (secrets.includes(name) || SECRET_NAME.test(name)) {
      delete env[name];
    }
  }
}

/** What a PM's shell needs to run through the wrapper, worked out once at plugin start. */
export interface PmIsolation {
  /**
   * The wrapper a PM's shell runs: the copy at `<serveDir>/isolate/pm-shell-isolate.sh`. A PM
   * cannot reach the copy (the serve folder is an empty tmpfs in its namespace), and the source in
   * `<covenRoot>/hooks` is a file a PM could edit in its own checkout.
   */
  wrapper: string;
  /** The profile's serve folder and data folder, absolute and `:`-joined. */
  hide: string;
  /** Whether the wrapper is ready; when not, every PM shell is refused with `problem`. */
  found: boolean;
  /** Why `found` is false, as the refusal names it. */
  problem: string | undefined;
}

/**
 * The wrapper a PM's shell runs and the folders it hides: the plugin's `serveDir` and `dataDir`
 * options, which setup writes from the Python `Layout`. The wrapper is copied from
 * `<covenRoot>/hooks` into the serve folder, over what an earlier start left there.
 */
export function pmIsolation(
  options: Pick<CovenOptions, "covenRoot" | "serveDir" | "dataDir">,
): PmIsolation {
  const source = join(options.covenRoot, "hooks", WRAPPER_NAME);
  const wrapper = join(options.serveDir, COPY_DIR, WRAPPER_NAME);
  const problem = isExecutableFile(source)
    ? copyProblem(source, wrapper)
    : `wrapper not found: ${source}`;
  return {
    wrapper,
    hide: [options.serveDir, options.dataDir].join(":"),
    found: problem === undefined,
    problem,
  };
}

function isExecutableFile(path: string): boolean {
  try {
    if (!statSync(path).isFile()) {
      return false;
    }
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** `undefined` when the copy was made, else why not. */
function copyProblem(source: string, copy: string): string | undefined {
  try {
    copyWrapper(source, copy);
    return undefined;
  } catch (error) {
    return `wrapper copy failed: ${messageOf(error)}`;
  }
}

/**
 * Copies the wrapper to `copy`: its folder 0700, the file 0500, an old file removed first (the
 * file is created, never opened through a link a leftover could be).
 */
function copyWrapper(source: string, copy: string): void {
  const folder = dirname(copy);
  try {
    mkdirSync(folder, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
      throw error;
    }
  }
  if (!lstatSync(folder).isDirectory()) {
    throw new Error(`${folder} is not a folder`);
  }
  chmodSync(folder, 0o700);
  rmSync(copy, { force: true });
  writeFileSync(copy, readFileSync(source), { mode: 0o700, flag: "wx" });
  chmodSync(copy, 0o500);
}

/** The refusal a throw from `createBefore` shows: the wrapper's own line, with the cause. */
function isolationRefusal(piece: string): Error {
  return new Error(
    `coven: PM shells run isolated and isolation is not available here: ${piece}; ask the user`,
  );
}

/** Points a PM's shell at the wrapper; throws, and leaves the event as it is, when it cannot. */
function isolate(event: ShellCreateEvent, isolation: PmIsolation): void {
  if (!isolation.found) {
    throw isolationRefusal(isolation.problem ?? "the wrapper is not ready");
  }
  const { shell } = event;
  if (shell === undefined || !isAbsolute(shell)) {
    throw isolationRefusal(
      `the shell is not an absolute path (${shell === undefined || shell === "" ? "none" : shell})`,
    );
  }
  event.env[REAL_SHELL_ENV] = shell;
  event.env[HIDE_ENV] = isolation.hide;
  event.shell = isolation.wrapper;
}

export interface ShellEnvDeps {
  keyEnv: string;
  /** The wrapper and the folders it hides, for a PM's shell. */
  isolation: PmIsolation;
  /** The location directory: the `cwd` of a shell call that gave no `workdir`. */
  directory: string;
  queue: ShellQueue;
  /** Shell events already taken for, shared by every plugin copy (`SharedState.taken`). */
  taken: WeakSet<object>;
  now: () => number;
}

/** The `command` and `workdir` strings of a shell call, or `undefined` when it has no command. */
function shellCall(
  event: ExecuteBeforeEvent,
  directory: string,
): { command: string; cwd: string; alsoCwd?: string } | undefined {
  const input: unknown = event.input;
  if (!SHELL_TOOLS.has(event.tool) || !isPlainObject(input)) {
    return undefined;
  }
  const { command, workdir } = input;
  if (typeof command !== "string") {
    return undefined;
  }
  const given = givenWorkdir(workdir);
  if (given !== undefined) {
    return { command, cwd: given };
  }
  // an empty workdir is no workdir, as the guard's `callCwd` reads it; which string the shell
  // event then carries (`""` or the location directory) is not known, so a call keeps both
  return workdir === ""
    ? { command, cwd: directory, alsoCwd: "" }
    : { command, cwd: directory };
}

/**
 * The two shell hooks. `executeBefore` notes which role a shell call belongs to (the call's one
 * role lookup, shared with the guards); `createBefore`
 * scrubs the environment of the shell that starts, then gives a PM's shell `ROLE_ENV` and points
 * it at the isolation wrapper (the real shell and the folders to hide go in the env). A throw
 * from `createBefore` stops the shell: a PM's shell is refused when the wrapper was not found or
 * its real shell is not an absolute path. Every plugin copy hooks the same shell event, so only
 * the first to run takes a queue entry; each still scrubs. A GM's shell gets no `ROLE_ENV`: the memory store's
 * pre-commit hook refuses a commit whenever it is set, and the GM writes the store.
 */
export function shellEnvHooks(deps: ShellEnvDeps): {
  executeBefore(event: ExecuteBeforeEvent, role: RoleLookup): Promise<void>;
  createBefore(event: ShellCreateEvent): void;
} {
  return {
    async executeBefore(event, role) {
      const call = shellCall(event, deps.directory);
      if (call === undefined) {
        return;
      }
      deps.queue.record({ role: (await role())?.role, ...call }, deps.now());
    },
    createBefore(event) {
      scrubEnv(event.env, deps.keyEnv);
      if (deps.taken.has(event)) {
        return;
      }
      deps.taken.add(event);
      const role = deps.queue.take(event.command, event.cwd, deps.now());
      if (role === "pm") {
        isolate(event, deps.isolation);
        event.env[ROLE_ENV] = role;
      }
    },
  };
}
