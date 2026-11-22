import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import { readHistory, sessionOf } from "../harness/history.ts";
import type { LeadHistory } from "../harness/history.ts";
import { cleanEnv, exec } from "../harness/process.ts";

export interface Harness {
  binary: string;
  project: string;
  root: string;
}

const KILL_AFTER_MS = 5 * 60 * 1000;

/** Runs the lead on `prompt` and returns its history as OpenCode exports it. */
export async function runScenario(
  harness: Harness,
  name: string,
  prompt: string,
): Promise<LeadHistory> {
  const home = join(harness.root, name);
  await mkdir(join(home, "tmp"), { recursive: true });
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
