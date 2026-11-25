import type {
  ExecuteBeforeEvent,
  SessionLike,
  ShellCreateEvent,
} from "../context.ts";
import { isPlainObject } from "../join/fields.ts";
import { ROLE_ENV, resolveRole } from "./role.ts";
import type { Role } from "./role.ts";

/** How long a recorded call waits for its shell to start. */
const EXPIRY_MS = 30_000;
const SERVER_PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD";
const PROFILE_PASSWORD_ENV = "OPENCODE_PASSWORD";
const SECRET_NAME = /^OPENCODE(_.*)?_(PASSWORD|TOKEN|KEY)$/;
const SHELL_TOOLS: ReadonlySet<string> = new Set(["shell", "bash"]);
/** The role a shell gets when its recorded calls disagree: the strictest. */
const MIXED: Role = "pm";

export interface ShellCall {
  sessionID: string;
  role: Role | undefined;
  command: string;
  cwd: string;
}

interface Entry {
  role: Role | undefined;
  at: number;
}

/**
 * The shell calls `execute.before` has seen and `create.before` has not started yet. The second
 * hook carries no session id, so a shell is paired to its call by what both hooks share: the
 * command and the `cwd` string as given.
 */
export class ShellQueue {
  private readonly entries = new Map<string, Entry[]>();

  record(call: ShellCall, now: number): void {
    const key = keyOf(call.command, call.cwd);
    const live = this.live(key, now);
    live.push({ role: call.role, at: now });
    this.entries.set(key, live);
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
    if (live.length === 0) {
      this.entries.delete(key);
    } else {
      this.entries.set(key, live);
    }
    return mixed ? MIXED : taken?.role;
  }

  private live(key: string, now: number): Entry[] {
    return (this.entries.get(key) ?? []).filter(
      (entry) => now - entry.at < EXPIRY_MS,
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

/** Deletes what a model's shell must not see: the server password and the provider key. */
export function scrubEnv(
  env: Record<string, string | undefined>,
  keyEnv: string,
): void {
  const secrets = [SERVER_PASSWORD_ENV, PROFILE_PASSWORD_ENV, keyEnv];
  for (const name of Object.keys(env)) {
    if (secrets.includes(name) || SECRET_NAME.test(name)) {
      delete env[name];
    }
  }
}

export interface ShellEnvDeps {
  keyEnv: string;
  /** The location directory: the `cwd` of a shell call that gave no `workdir`. */
  directory: string;
  queue: ShellQueue;
  /** Shell events already taken for, shared by every plugin copy (`SharedState.taken`). */
  taken: WeakSet<object>;
  now: () => number;
  get: (id: string) => Promise<SessionLike | undefined>;
}

/** The `command` and `workdir` strings of a shell call, or `undefined` when it has no command. */
function shellCall(
  event: ExecuteBeforeEvent,
  directory: string,
): { command: string; cwd: string } | undefined {
  const input: unknown = event.input;
  if (!SHELL_TOOLS.has(event.tool) || !isPlainObject(input)) {
    return undefined;
  }
  const { command, workdir } = input;
  if (typeof command !== "string") {
    return undefined;
  }
  // an empty workdir is no workdir: the guard's `callCwd` reads it the same way
  const given = typeof workdir === "string" && workdir !== "";
  return { command, cwd: given ? workdir : directory };
}

/**
 * The two shell hooks. `executeBefore` notes which role a shell call belongs to; `createBefore`
 * scrubs the environment of the shell that starts, then gives a PM's shell `ROLE_ENV`. A throw
 * from `createBefore` stops the shell. Every plugin copy hooks the same shell event, so only
 * the first to run takes a queue entry; each still scrubs. A GM's shell gets no `ROLE_ENV`: the memory store's
 * pre-commit hook refuses a commit whenever it is set, and the GM writes the store.
 */
export function shellEnvHooks(deps: ShellEnvDeps): {
  executeBefore(event: ExecuteBeforeEvent): Promise<void>;
  createBefore(event: ShellCreateEvent): void;
} {
  return {
    async executeBefore(event) {
      const call = shellCall(event, deps.directory);
      if (call === undefined) {
        return;
      }
      const info = await resolveRole(event.sessionID, deps.get);
      deps.queue.record(
        { sessionID: event.sessionID, role: info?.role, ...call },
        deps.now(),
      );
    },
    createBefore(event) {
      scrubEnv(event.env, deps.keyEnv);
      if (deps.taken.has(event)) {
        return;
      }
      deps.taken.add(event);
      const role = deps.queue.take(event.command, event.cwd, deps.now());
      if (role === "pm") {
        event.env[ROLE_ENV] = role;
      }
    },
  };
}
