import { awaitResults, childIDs } from "./history.ts";
import type { AwaitResult, LeadHistory, SubagentReport } from "./history.ts";

/** What each scenario left in the lead's history. */
export interface Runs {
  all: LeadHistory;
  any: LeadHistory;
  long: LeadHistory;
  nested: LeadHistory;
  unknown: LeadHistory;
}

export const LONG_SLEEP_SECONDS = 120;
const NESTED_SLEEP_SECONDS = 8;

/** A reason the check failed, or undefined when it passed. */
export type Problem = string | undefined;

export interface Check {
  title: string;
  run(runs: Runs): Problem;
}

function firstAwait(history: LeadHistory): AwaitResult | string {
  return awaitResults(history)[0] ?? "no await_agents call completed";
}

function finished(history: LeadHistory): Problem {
  return history.finalText.includes("PARENT DONE")
    ? undefined
    : `the lead did not finish: ${history.finalText.slice(0, 120)}`;
}

function allReturns(history: LeadHistory): Problem {
  const result = firstAwait(history);
  if (typeof result === "string") {
    return `all: ${result}`;
  }
  if (
    result.done.length !== 3 ||
    result.pending.length > 0 ||
    result.timedOut
  ) {
    return `all: expected 3 done and none pending, got ${JSON.stringify(result)}`;
  }
  return finished(history);
}

function anyReturns(history: LeadHistory): Problem {
  const result = firstAwait(history);
  if (typeof result === "string") {
    return `any: ${result}`;
  }
  if (result.done.length < 1 || result.pending.length < 1 || result.timedOut) {
    return `any: expected a first return with some pending, got ${JSON.stringify(result)}`;
  }
  const seen = new Set(
    awaitResults(history).flatMap((r) => r.done.map((d) => d.sessionID)),
  );
  return seen.size === 3
    ? finished(history)
    : `any: ${seen.size} of 3 children returned`;
}

function onceEach(name: string, history: LeadHistory, count: number): Problem {
  const ids = childIDs(history);
  if (ids.length !== count) {
    return `${name}: expected ${count} children, found ${ids.length}`;
  }
  for (const id of ids) {
    const seen = history.reports.filter((r) => r.childID === id).length;
    if (seen !== 1) {
      return `${name}: ${id} has ${seen} <subagent> messages`;
    }
  }
  return undefined;
}

/** The last `<subagent>` message the lead's history holds for one child. */
function lastReport(
  history: LeadHistory,
  id: string,
): SubagentReport | undefined {
  return history.reports.filter((r) => r.childID === id).at(-1);
}

function longWait(history: LeadHistory): Problem {
  const result = firstAwait(history);
  if (typeof result === "string") {
    return result;
  }
  const child = result.done[0];
  if (child === undefined || result.pending.length > 0 || result.timedOut) {
    return `the wait ended early: ${JSON.stringify(result)}`;
  }
  if (result.waitedSeconds < LONG_SLEEP_SECONDS - 2) {
    return `returned after ${result.waitedSeconds} s`;
  }
  const report = lastReport(history, child.sessionID);
  return report?.text.includes(`slept ${LONG_SLEEP_SECONDS}s`) === true
    ? finished(history)
    : `the report is missing from the history: ${JSON.stringify(report)}`;
}

function nestedWait(history: LeadHistory): Problem {
  const calls = awaitResults(history);
  const child = calls[0]?.done[0];
  if (calls.length !== 1 || child === undefined) {
    return `expected one wait that returns the worker, got ${JSON.stringify(calls)}`;
  }
  const waited = calls[0]?.waitedSeconds ?? 0;
  if (child.state !== "completed" || waited < NESTED_SLEEP_SECONDS - 1) {
    return `the wait returned early (${waited} s): ${JSON.stringify(child)}`;
  }
  const report = lastReport(history, child.sessionID);
  return report?.text.includes("final after") === true
    ? finished(history)
    : `the final report is missing from the history: ${JSON.stringify(report)}`;
}

function unknownID(history: LeadHistory): Problem {
  const tool = history.tools.find((t) => t.name === "await_agents");
  if (tool === undefined) {
    return "the lead never called await_agents";
  }
  return tool.output.includes("ses_doesnotexist")
    ? undefined
    : `the result does not name the id: ${tool.output.slice(0, 200)}`;
}

export const CHECKS: readonly Check[] = [
  {
    title: "all and any return with 3 parallel workers",
    run: (runs) => allReturns(runs.all) ?? anyReturns(runs.any),
  },
  {
    title: "each worker report appears exactly once in the lead's history",
    run: (runs) =>
      onceEach("all", runs.all, 3) ??
      onceEach("any", runs.any, 3) ??
      onceEach("long", runs.long, 1),
  },
  {
    title: `a worker that sleeps ${LONG_SLEEP_SECONDS} s is waited for`,
    run: (runs) => longWait(runs.long),
  },
  {
    title: "a worker with its own worker: the wait ends after the final report",
    run: (runs) => nestedWait(runs.nested),
  },
  {
    title: "an unknown id is named in the tool result",
    run: (runs) => unknownID(runs.unknown),
  },
];
