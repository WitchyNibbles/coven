import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";

import { covenCommand } from "./coven.ts";

export interface Api {
  get(path: string): Promise<unknown>;
  post(path: string): Promise<unknown>;
}

interface ServerOptions {
  cwd: string;
  env: Record<string, string>;
}

const START_TIMEOUT_MS = 30_000;
const GRACE_MS = 5000;
const REQUEST_TIMEOUT_MS = 10_000;
const USERNAME = "opencode";
const LISTENING = /^server listening on (http:\/\/127\.0\.0\.1:\d+)$/;

/** Reads the child's stdout and settles on the URL of its listening line. */
function listeningURL(child: ChildProcess): Promise<string> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("the server did not start within 30 s"));
    }, START_TIMEOUT_MS);
    let pending = "";
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (part: string) => {
      pending += part;
      const lines = pending.split("\n");
      pending = lines.pop() ?? "";
      const found = lines
        .map((line) => LISTENING.exec(line.trim())?.[1])
        .find((url) => url !== undefined);
      if (found !== undefined) {
        clearTimeout(timer);
        resolve(found);
      }
    });
    child.stderr?.resume();
    child.once("error", reject);
    child.once("close", () => {
      clearTimeout(timer);
      reject(new Error("the server exited before it listened"));
    });
  });
}

/** SIGTERM, then SIGKILL after the grace period; resolves once the child is gone. */
function stop(child: ChildProcess): Promise<void> {
  const gone = child.exitCode !== null || child.signalCode !== null;
  if (gone || child.pid === undefined) {
    return Promise.resolve();
  }
  return new Promise((done) => {
    const killer = setTimeout(() => child.kill("SIGKILL"), GRACE_MS);
    child.once("close", () => {
      clearTimeout(killer);
      done();
    });
    child.kill("SIGTERM");
  });
}

async function request(
  method: string,
  url: string,
  password: string,
): Promise<unknown> {
  const token = Buffer.from(`${USERNAME}:${password}`).toString("base64");
  const response = await fetch(url, {
    method,
    headers: { authorization: `Basic ${token}` },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(
      `${method} ${new URL(url).pathname} returned HTTP ${response.status}`,
    );
  }
  return body.trim() === "" ? undefined : (JSON.parse(body) as unknown);
}

function apiFor(base: string, password: string): Api {
  return {
    get: (path) => request("GET", `${base}${path}`, password),
    post: (path) => request("POST", `${base}${path}`, password),
  };
}

/**
 * Runs `coven opencode run -- serve` on a free loopback port with a random password, calls
 * `body` with an authenticated client, and always stops the server afterwards.
 */
export async function withServer<T>(
  options: ServerOptions,
  body: (api: Api) => Promise<T>,
): Promise<T> {
  const password = randomBytes(18).toString("base64url");
  const run = covenCommand([
    "opencode",
    "run",
    "--",
    "serve",
    "--hostname",
    "127.0.0.1",
    "--port",
    "0",
  ]);
  const child = spawn(run.command, run.args, {
    cwd: options.cwd,
    env: { ...options.env, OPENCODE_SERVER_PASSWORD: password },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    return await body(apiFor(await listeningURL(child), password));
  } finally {
    await stop(child);
  }
}
