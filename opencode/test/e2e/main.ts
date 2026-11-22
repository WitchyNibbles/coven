/**
 * The end-to-end run of the join on OpenCode 2.3.2 with the scripted fake model.
 * Run it with `npm run e2e`: it does nothing unless COVEN_E2E=1, and it refuses to start
 * outside a network namespace that has only the loopback interface.
 */
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { CHECKS } from "./checks.ts";
import type { Runs } from "./checks.ts";
import { startFakeModel } from "./fake-model.ts";
import {
  configProblem,
  NAMESPACE_MESSAGE,
  onlyLoopback,
  parseConfig,
} from "./guards.ts";
import type { LeadHistory } from "./history.ts";
import { runScenario } from "./opencode.ts";
import type { Harness } from "./opencode.ts";

const PROJECT = fileURLToPath(new URL("./project", import.meta.url));

const PROMPTS: Record<keyof Runs, string> = {
  all: "PARENT mode=all sleeps=3,6,9 child=CHILD",
  any: "PARENT mode=any sleeps=2,8,14 child=CHILD",
  long: "PARENT mode=all sleeps=120 child=CHILD",
  nested: "PARENT mode=all sleeps=8 child=NEST",
  unknown: "PARENT mode=unknown",
};

const EMPTY: LeadHistory = { reports: [], tools: [], finalText: "" };

function bringLoopbackUp(): boolean {
  return spawnSync("ip", ["link", "set", "lo", "up"]).status === 0;
}

async function runAll(harness: Harness): Promise<Runs> {
  const names = Object.keys(PROMPTS) as (keyof Runs)[];
  const settled = await Promise.allSettled(
    names.map((name) => runScenario(harness, name, PROMPTS[name])),
  );
  const runs: Runs = {
    all: EMPTY,
    any: EMPTY,
    long: EMPTY,
    nested: EMPTY,
    unknown: EMPTY,
  };
  settled.forEach((outcome, index) => {
    const name = names[index] ?? "all";
    if (outcome.status === "fulfilled") {
      runs[name] = outcome.value;
    } else {
      console.log(`# scenario ${name} failed: ${String(outcome.reason)}`);
    }
  });
  return runs;
}

function report(runs: Runs): number {
  let failures = 0;
  CHECKS.forEach((check, index) => {
    const problem = check.run(runs);
    failures += problem === undefined ? 0 : 1;
    const head = problem === undefined ? "ok" : "not ok";
    console.log(
      `${head} ${index + 1} - ${check.title}${problem === undefined ? "" : `: ${problem}`}`,
    );
  });
  return failures;
}

async function main(): Promise<number> {
  if (!onlyLoopback(await readFile("/proc/net/dev", "utf8"))) {
    console.error(NAMESPACE_MESSAGE);
    return 1;
  }
  const binary = process.env["COVEN_OPENCODE_BIN"];
  if (binary === undefined || binary === "") {
    console.log("skipped: set COVEN_OPENCODE_BIN");
    return 0;
  }
  const text = await readFile(join(PROJECT, "opencode.jsonc"), "utf8");
  const problem = configProblem(parseConfig(text));
  if (problem !== undefined || !bringLoopbackUp()) {
    console.error(problem ?? "could not bring the loopback interface up");
    return 1;
  }
  const root = await mkdtemp(join(tmpdir(), "coven-e2e-"));
  const model = await startFakeModel();
  try {
    return report(await runAll({ binary, project: PROJECT, root })) === 0
      ? 0
      : 1;
  } finally {
    await model.close();
    await rm(root, { recursive: true, force: true });
  }
}

if (process.env["COVEN_E2E"] === "1") {
  process.exitCode = await main();
}
