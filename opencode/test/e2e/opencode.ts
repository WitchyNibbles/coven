import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import { readHistory } from "./history.ts";
import type { LeadHistory } from "./history.ts";

export interface Harness {
  binary: string;
  project: string;
  root: string;
}

interface Finished {
  code: number | null;
  out: string;
  err: string;
}

const KILL_AFTER_MS = 5 * 60 * 1000;

/** An environment built from nothing, so no key or setting of the caller reaches OpenCode. */
function cleanEnv(home: string): Record<string, string> {
  return {
    PATH: process.env["PATH"] ?? "",
    HOME: home,
    XDG_CONFIG_HOME: join(home, ".config"),
    XDG_DATA_HOME: join(home, ".local", "share"),
    XDG_STATE_HOME: join(home, ".local", "state"),
    XDG_CACHE_HOME: join(home, ".cache"),
    OPENCODE_DISABLE_AUTOUPDATE: "1",
  };
}

function exec(
  command: string,
  args: string[],
  options: { cwd: string; env: Record<string, string>; timeoutMs: number },
): Promise<Finished> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (part: Buffer) => {
      out += part.toString("utf8");
    });
    child.stderr.on("data", (part: Buffer) => {
      err += part.toString("utf8");
    });
    const timer = setTimeout(() => child.kill("SIGKILL"), options.timeoutMs);
    child.once("error", reject);
    child.once("close", (code) => {
      clearTimeout(timer);
      resolve({ code, out, err });
    });
  });
}

function sessionOf(out: string): string {
  const first = out.split("\n").find((line) => line.startsWith("{")) ?? "{}";
  const id = (JSON.parse(first) as { sessionID?: string }).sessionID;
  if (id === undefined) {
    throw new Error(`the run printed no session id: ${out.slice(0, 300)}`);
  }
  return id;
}

/** Runs the lead on `prompt` and returns its history as OpenCode exports it. */
export async function runScenario(
  harness: Harness,
  name: string,
  prompt: string,
): Promise<LeadHistory> {
  const home = join(harness.root, name);
  await mkdir(home, { recursive: true });
  const options = {
    cwd: harness.project,
    env: cleanEnv(home),
    timeoutMs: KILL_AFTER_MS,
  };
  const run = await exec(
    harness.binary,
    [
      "run",
      "--standalone",
      "--format",
      "json",
      "--agent",
      "lead",
      "--model",
      "fake/fake",
      prompt,
    ],
    options,
  );
  if (run.code !== 0) {
    throw new Error(
      `run exited ${run.code}: ${(run.err || run.out).slice(-400)}`,
    );
  }
  const exported = await exec(
    harness.binary,
    ["session", "export", "--standalone", sessionOf(run.out)],
    options,
  );
  if (exported.code !== 0) {
    throw new Error(
      `export exited ${exported.code}: ${exported.err.slice(-400)}`,
    );
  }
  return readHistory(exported.out);
}
