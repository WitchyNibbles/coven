import { spawn } from "node:child_process";
import { join } from "node:path";

import type { ExecuteBeforeEvent } from "./context.ts";
import { payloads } from "./deny-secrets.ts";
import type { Payload } from "./deny-secrets.ts";
import { messageOf } from "./join/fields.ts";

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

export type SpawnFn = (command: string, args: readonly string[]) => GuardChild;

const MAX_MESSAGE = 2000;
/** Stderr kept for the message; more than the message can show. */
const MAX_STDERR = 100_000;

/** The server's own environment goes to the child; the guard runs without a shell string. */
const spawnShell: SpawnFn = (command, args) =>
  spawn(command, args, { stdio: ["pipe", "ignore", "pipe"] });

function cut(message: string): Error {
  return new Error(message.slice(0, MAX_MESSAGE));
}

function startFailure(error: unknown): Error {
  return cut(
    `coven deny-secrets: could not start the guard (${messageOf(error)}); blocking`,
  );
}

/** `undefined` when the guard allowed the call, else why it did not. */
function verdict(
  code: number | null,
  signal: string | null,
  stderr: string,
): Error | undefined {
  if (code === 0) {
    return undefined;
  }
  if (code === 2) {
    return cut(stderr.trim() || "coven deny-secrets: blocked");
  }
  const how = code === null ? `signal ${signal}` : `exit ${code}`;
  return cut(`coven deny-secrets: guard failed (${how}); blocking`);
}

/** The arguments of `sh`: `fail-closed.sh` and the Python guard it runs. */
function scriptArgs(covenRoot: string): readonly string[] {
  return [
    join(covenRoot, "hooks", "fail-closed.sh"),
    join(covenRoot, "hooks", "deny_secrets.py"),
  ];
}

function runScript(
  args: readonly string[],
  payload: Payload,
  timeoutMs: number,
  spawnFn: SpawnFn,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let child: GuardChild;
    try {
      child = spawnFn("sh", args);
    } catch (error) {
      reject(startFailure(error));
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
        cut(
          `coven deny-secrets: guard timed out after ${timeoutMs} ms; blocking`,
        ),
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
      finish(startFailure(error));
    });
    child.on("close", (code, signal) => {
      finish(verdict(code, signal, stderr));
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

/**
 * Runs the Python guard on one payload through `fail-closed.sh`. Resolves when it allows; rejects
 * with the text the model should see when it refuses, fails, times out or cannot start.
 */
export function runGuard(
  covenRoot: string,
  payload: Payload,
  timeoutMs: number,
  spawnFn: SpawnFn = spawnShell,
): Promise<void> {
  return runScript(scriptArgs(covenRoot), payload, timeoutMs, spawnFn);
}

export interface DenySecretsOptions {
  covenRoot: string;
  guardTimeoutMs: number;
}

export interface DenySecretsDeps {
  /** The session's directory: relative paths in a call resolve against it. */
  directory: string;
  spawn?: SpawnFn;
}

/**
 * The `execute.before` hook. It never catches: a refusal, a crash of the guard and a timeout all
 * throw, which fails the tool call before OpenCode asks for permission.
 */
export function denySecretsHook(
  options: DenySecretsOptions,
  deps: DenySecretsDeps,
): (event: ExecuteBeforeEvent) => Promise<void> {
  const args = scriptArgs(options.covenRoot);
  return async (event) => {
    const checked = payloads(event.tool, event.input, deps.directory);
    if (checked === "unchecked") {
      return;
    }
    for (const payload of checked) {
      await runScript(
        args,
        payload,
        options.guardTimeoutMs,
        deps.spawn ?? spawnShell,
      );
    }
  };
}
