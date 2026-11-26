/**
 * The end-to-end runs on OpenCode 2.3.2 with the scripted fake model.
 * Run it with `npm run e2e [-- runtime|join]`: `npm run e2e` enters a network namespace with only the
 * loopback interface and starts this file there. It does nothing unless COVEN_E2E=1, and it
 * refuses to start outside that namespace.
 */
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { startFakeModel } from "../harness/fake-model.ts";
import { configProblem, parseConfig } from "../harness/guards.ts";
import type { LeadHistory } from "../harness/history.ts";
import {
  bringLoopbackUp,
  NAMESPACE_MESSAGE,
  onlyLoopback,
} from "../harness/namespace.ts";
import { CHECKS } from "./checks.ts";
import type { Runs } from "./checks.ts";
import { runScenario } from "./join-run.ts";
import type { Harness } from "./join-run.ts";
import { evaluateSuite } from "./evaluate/checks.ts";
import { pmApiSuite } from "./pm-api/checks.ts";
import { runtimeSuite } from "./runtime/checks.ts";
import type { Outcome } from "./runtime/checks.ts";
import { decideIn } from "./script.ts";

const PROJECT = fileURLToPath(new URL("./project", import.meta.url));

const PROMPTS: Record<keyof Runs, string> = {
  all: "PARENT mode=all sleeps=3,6,9 child=CHILD",
  any: "PARENT mode=any sleeps=2,8,14 child=CHILD",
  long: "PARENT mode=all sleeps=120 child=CHILD",
  nested: "PARENT mode=all sleeps=8 child=NEST",
  unknown: "PARENT mode=unknown",
};

const EMPTY: LeadHistory = { reports: [], tools: [], finalText: "" };

type Suite = (harness: Harness) => Promise<Outcome[]>;

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

const joinSuite: Suite = async (harness) => {
  const runs = await runAll(harness);
  return CHECKS.map((check) => ({
    title: check.title,
    problem: check.run(runs),
  }));
};

/** The suites, in the order they run when none is named: the runtime suite comes first. */
const SUITES: Record<string, Suite> = {
  runtime: runtimeSuite,
  join: joinSuite,
  evaluate: evaluateSuite,
  "pm-api": pmApiSuite,
};

/** The suites to run, or the refusal when one is not known. */
function chosenSuites(argv: readonly string[]): readonly string[] | string {
  const names = argv.length > 0 ? argv : Object.keys(SUITES);
  const unknown = names.find((name) => !Object.hasOwn(SUITES, name));
  return unknown === undefined
    ? names
    : `unknown suite ${unknown}; available: ${Object.keys(SUITES).join(", ")}`;
}

/** Prints the outcomes, numbered from `first`, and returns how many failed. */
function report(outcomes: readonly Outcome[], first: number): number {
  let failures = 0;
  outcomes.forEach((outcome, index) => {
    const failed = outcome.problem !== undefined;
    failures += failed ? 1 : 0;
    const detail = failed ? `: ${outcome.problem}` : "";
    const head = failed ? "not ok" : "ok";
    console.log(`${head} ${first + index} - ${outcome.title}${detail}`);
  });
  return failures;
}

async function runSuites(names: readonly string[], harness: Harness) {
  let failures = 0;
  let printed = 0;
  for (const name of names) {
    const outcomes = await (SUITES[name] as Suite)(harness);
    failures += report(outcomes, printed + 1);
    printed += outcomes.length;
  }
  return failures;
}

async function main(argv: readonly string[]): Promise<number> {
  if (!onlyLoopback(await readFile("/proc/net/dev", "utf8"))) {
    console.error(NAMESPACE_MESSAGE);
    return 1;
  }
  const binary = process.env["COVEN_OPENCODE_BIN"];
  if (binary === undefined || binary === "") {
    console.log("skipped: set COVEN_OPENCODE_BIN");
    return 0;
  }
  const names = chosenSuites(argv);
  if (typeof names === "string") {
    console.error(names);
    return 1;
  }
  const text = await readFile(join(PROJECT, "opencode.jsonc"), "utf8");
  const problem = configProblem(parseConfig(text));
  if (problem !== undefined || !bringLoopbackUp()) {
    console.error(problem ?? "could not bring the loopback interface up");
    return 1;
  }
  const root = await mkdtemp(join(tmpdir(), "coven-e2e-"));
  // the fake workers' shell commands run here, outside coven's checkout (where the project sits)
  const workdir = join(root, "work");
  await mkdir(workdir);
  const model = await startFakeModel({ decide: decideIn(workdir) });
  try {
    return (await runSuites(names, { binary, project: PROJECT, root })) === 0
      ? 0
      : 1;
  } finally {
    await model.close();
    await rm(root, { recursive: true, force: true });
  }
}

if (process.env["COVEN_E2E"] === "1") {
  process.exitCode = await main(process.argv.slice(2));
}
