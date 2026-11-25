import { spawn } from "node:child_process";
import { basename, join } from "node:path";

import type { ExecuteBeforeEvent } from "./context.ts";
import { payloads } from "./deny-secrets.ts";
import type { Payload } from "./deny-secrets.ts";
import { messageOf } from "./join/fields.ts";
import { inPmTree } from "./pm/asks.ts";
import { ROLE_ENV } from "./pm/role.ts";
import type { RoleLookup } from "./pm/role.ts";

/** The part of a child process the guard uses; `ChildProcess` fits, so a test can stand in for it. */
export interface GuardChild {
  readonly stdin: {
    end(data: string): unknown;
    on(event: "error", listener: () => void): unknown;
  };
  readonly stderr: {
    setEncoding(encoding: "utf8"): unknown;
    on(event: "data", listener: (chunk: string) => void): unknown;
  };
  on(event: "error", listener: (error: Error) => void): unknown;
  on(
    event: "close",
    listener: (code: number | null, signal: string | null) => void,
  ): unknown;
  kill(signal: "SIGKILL"): unknown;
}

/** `env` is added to the server's own environment for that child; none leaves it as it is. */
export type SpawnFn = (
  command: string,
  args: readonly string[],
  env?: NodeJS.ProcessEnv,
) => GuardChild;

/**
 * How long each hook may run, as in `hooks/hooks.json`. Deny-secrets' own timeout is the
 * `guardTimeoutMs` option, which starts at 5 000 ms.
 */
export const HOOK_TIMEOUTS_MS = {
  serverGuard: 5000,
  pmPermissions: 15_000,
  prodGuard: 30_000,
} as const;

const MAX_MESSAGE = 2000;
/** Stderr kept for the message; more than the message can show. */
const MAX_STDERR = 100_000;

/** The server's own environment goes to the child, plus `env`; no shell string is ever built. */
const spawnShell: SpawnFn = (command, args, env) =>
  spawn(command, args, {
    stdio: ["pipe", "ignore", "pipe"],
    ...(env === undefined ? {} : { env: { ...process.env, ...env } }),
  });

function cut(message: string): Error {
  return new Error(message.slice(0, MAX_MESSAGE));
}

/** The name a refusal carries: `pm_permissions.py` is `pm-permissions`. */
function labelOf(script: string): string {
  return basename(script, ".py").replaceAll("_", "-");
}

function startFailure(label: string, error: unknown): Error {
  return cut(
    `coven ${label}: could not start the guard (${messageOf(error)}); blocking`,
  );
}

/** `undefined` when the guard allowed the call, else why it did not. */
function verdict(
  label: string,
  code: number | null,
  signal: string | null,
  stderr: string,
): Error | undefined {
  if (code === 0) {
    return undefined;
  }
  if (code === 2) {
    return cut(stderr.trim() || `coven ${label}: blocked`);
  }
  const how = code === null ? `signal ${signal}` : `exit ${code}`;
  return cut(`coven ${label}: guard failed (${how}); blocking`);
}

interface Run {
  command: string;
  args: readonly string[];
  /** What the child reads on stdin. */
  input: string;
  label: string;
  timeoutMs: number;
  env: NodeJS.ProcessEnv | undefined;
  spawn: SpawnFn;
}

function runChild(run: Run): Promise<void> {
  const { label, timeoutMs } = run;
  return new Promise((resolve, reject) => {
    let child: GuardChild;
    try {
      child = run.spawn(run.command, run.args, run.env);
    } catch (error) {
      reject(startFailure(label, error));
      return;
    }
    let stderr = "";
    let over = false;
    const finish = (outcome: Error | undefined): void => {
      if (over) {
        return;
      }
      over = true;
      clearTimeout(timer);
      if (outcome === undefined) {
        resolve();
      } else {
        reject(outcome);
      }
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(
        cut(`coven ${label}: guard timed out after ${timeoutMs} ms; blocking`),
      );
    }, timeoutMs);
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      if (stderr.length < MAX_STDERR) {
        stderr = (stderr + chunk).slice(0, MAX_STDERR);
      }
    });
    // A guard that exits before reading breaks the pipe; its exit code still decides.
    child.stdin.on("error", () => undefined);
    child.on("error", (error) => {
      finish(startFailure(label, error));
    });
    child.on("close", (code, signal) => {
      finish(verdict(label, code, signal, stderr));
    });
    child.stdin.end(run.input);
  });
}

/**
 * Runs one hook script of coven's `hooks/` folder on a payload through `fail-closed.sh`, with
 * `extraArgs` after the script and `env` added to the child's environment. Resolves when it
 * allows; rejects with the text the model should see when it refuses, fails, times out or
 * cannot start.
 */
export function runHookScript(
  covenRoot: string,
  script: string,
  extraArgs: readonly string[],
  payload: Payload,
  timeoutMs: number,
  env?: NodeJS.ProcessEnv,
  spawnFn: SpawnFn = spawnShell,
): Promise<void> {
  return runChild({
    command: "sh",
    args: [
      join(covenRoot, "hooks", "fail-closed.sh"),
      join(covenRoot, "hooks", script),
      ...extraArgs,
    ],
    input: JSON.stringify(payload),
    label: labelOf(script),
    timeoutMs,
    env,
    spawn: spawnFn,
  });
}

const MIN_PYTHON = "(3, 12)";
const PYTHON_CHECK_TIMEOUT_MS = 5000;

/**
 * Refuses unless `python3` is on the path and is 3.12 or newer. Every hook needs it, and
 * pm-permissions would allow a call it could not run, so the plugin does not load without it.
 */
export async function checkPython(
  spawnFn: SpawnFn = spawnShell,
): Promise<void> {
  try {
    await runChild({
      command: "python3",
      args: [
        "-c",
        `import sys; sys.exit(0 if sys.version_info >= ${MIN_PYTHON} else 1)`,
      ],
      input: "",
      label: "python3",
      timeoutMs: PYTHON_CHECK_TIMEOUT_MS,
      env: undefined,
      spawn: spawnFn,
    });
  } catch (error) {
    throw new Error(
      `coven: the plugin needs python3 3.12 or newer on the path, and it was not usable (${messageOf(error)})`,
      { cause: error },
    );
  }
}

export interface GuardsOptions {
  covenRoot: string;
  /** deny-secrets' own timeout. */
  guardTimeoutMs: number;
  profileDir: string;
  serveDir: string;
}

export interface GuardsDeps {
  /** The session's directory: relative paths in a call resolve against it. */
  directory: string;
  spawn?: SpawnFn;
}

/** The child env of the PM-tree hooks: the role variable the Claude Code hooks read. */
const PM_ENV: NodeJS.ProcessEnv = { [ROLE_ENV]: "pm" };

/** One hook script and how it runs. */
interface Step {
  script: string;
  extraArgs: readonly string[];
  timeoutMs: number;
  env?: NodeJS.ProcessEnv;
}

/**
 * The `execute.before` hook of every session. For each mapped payload it runs deny-secrets and
 * the server guard; a session in a PM tree (a failed role lookup included) also gets
 * pm-permissions and the production guard, which run without approvals and with the role set in
 * their env. `role` is the call's one role lookup, made only for a call the guards cover. The
 * first refusal throws, and the hooks after it do not run. It never catches: a refusal, a crash
 * of a guard and a timeout all throw, which fails the tool call before OpenCode asks for
 * permission.
 */
export function guardsHook(
  options: GuardsOptions,
  deps: GuardsDeps,
): (event: ExecuteBeforeEvent, role: RoleLookup) => Promise<void> {
  const { covenRoot } = options;
  const everySession: readonly Step[] = [
    {
      script: "deny_secrets.py",
      extraArgs: [],
      timeoutMs: options.guardTimeoutMs,
    },
    {
      script: "opencode_server_guard.py",
      extraArgs: [
        "--profile",
        options.profileDir,
        "--serve",
        options.serveDir,
        "--coven-root",
        covenRoot,
      ],
      timeoutMs: HOOK_TIMEOUTS_MS.serverGuard,
    },
  ];
  const pmTreeOnly: readonly Step[] = [
    {
      script: "pm_permissions.py",
      extraArgs: ["--pre-tool-use"],
      timeoutMs: HOOK_TIMEOUTS_MS.pmPermissions,
      env: PM_ENV,
    },
    {
      script: "prod_guard.py",
      extraArgs: ["--no-approvals"],
      timeoutMs: HOOK_TIMEOUTS_MS.prodGuard,
      env: PM_ENV,
    },
  ];
  return async (event, role) => {
    const checked = payloads(event.tool, event.input, deps.directory);
    if (checked === "unchecked") {
      return;
    }
    const steps = (await inPmTree(role))
      ? [...everySession, ...pmTreeOnly]
      : everySession;
    for (const payload of checked) {
      for (const step of steps) {
        await runHookScript(
          covenRoot,
          step.script,
          step.extraArgs,
          payload,
          step.timeoutMs,
          step.env,
          deps.spawn,
        );
      }
    }
  };
}
