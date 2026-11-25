/** What the hierarchy checks share: the GM session, the PMs made so far, and small readers of what a turn did. */
import { readFile } from "node:fs/promises";

import { ROLE_ENV } from "../../../src/pm/role.ts";
import {
  incoming,
  messagesOf,
  once,
  quiet,
  sleep,
  toolsIn,
  turn,
  waitIdleAfter,
  infoOf,
} from "./sessions.ts";
import type { ToolRun } from "./sessions.ts";
import type { HierarchyWorld } from "./world.ts";

/** A line of the e2e output that is no pass or fail: a value the controller rules on. */
export function record(key: string, value: string): void {
  console.log(`# record ${key}: ${value}`);
}

/**
 * A shell fragment that prints how many environment variables start with the role variable's
 * prefix, without naming the variable: a PM's production guard refuses any command that does.
 */
export const ROLE_COUNT = `$(env | grep -c ^${ROLE_ENV.slice(0, ROLE_ENV.lastIndexOf("_") + 1)})`;

export interface Run {
  world: HierarchyWorld;
  /** The GM session (made and claimed by the first check). */
  gm: string;
  /** The PM sessions launched so far, by code. */
  pms: Map<string, string>;
}

/** The tool call a check expects; `undefined` problem means it completed. */
export function completedProblem(run: ToolRun | undefined): string | undefined {
  if (run === undefined) {
    return "no tool call was made";
  }
  return run.status === "completed"
    ? undefined
    : `${run.name} ended ${run.status}: ${run.text.slice(0, 200)}`;
}

/** What is wrong with a call a guard should have refused: it must be an error naming `label`. */
export function refusalProblem(
  run: ToolRun | undefined,
  label: string,
): string | undefined {
  if (run === undefined) {
    return "no tool call was made";
  }
  if (run.status === "completed" || !run.text.includes(`coven ${label}`)) {
    return `${run.name} was ${run.status}: ${run.text.slice(0, 220)}`;
  }
  return undefined;
}

/** The GM's tool call; the GM must be settled first, which `turn` does. */
export function gmCall(
  run: Run,
  tool: string,
  args: Record<string, unknown>,
): Promise<ToolRun> {
  return once(run.world, run.gm, tool, args);
}

/** The session id of the PM with `code`; throws when no earlier check launched it. */
export function pmId(run: Run, code: string): string {
  const id = run.pms.get(code);
  if (id === undefined) {
    throw new Error(`no PM ${code}: its launch did not complete`);
  }
  return id;
}

/** Launches a PM through the GM's tool and notes its id; the brief runs on its own. */
export async function launch(
  run: Run,
  code: string,
  repo: string,
  brief: readonly [string, Record<string, unknown>][] = [],
  extraRepos: readonly string[] = [],
): Promise<ToolRun> {
  const lines = brief.map(
    ([tool, args]) => `DO ${tool} ${JSON.stringify(args)}`,
  );
  const result = await gmCall(run, "coven_pm_launch", {
    code,
    repo,
    brief: ["BRIEF", ...lines].join("\n"),
    ...(extraRepos.length > 0 ? { extraRepos } : {}),
  });
  const id = /session (ses_\w+)/.exec(result.text)?.[1];
  if (result.status === "completed" && id !== undefined) {
    run.pms.set(code, id);
  }
  return result;
}

/** Waits for the PM's first turn (its brief) to end and its queue to run dry. */
export async function settlePm(run: Run, code: string): Promise<void> {
  const id = pmId(run, code);
  await waitIdleAfter(run.world, id, 0);
  await quiet(run.world, id);
}

/** A PM turn on a prompt from the API (not from the GM), with the tool calls it made. */
export function pmTurn(
  run: Run,
  code: string,
  calls: readonly [string, Record<string, unknown>][],
): Promise<ToolRun[]> {
  return turn(run.world, pmId(run, code), calls);
}

/** One tool call in a PM's own session. */
export async function pmCall(
  run: Run,
  code: string,
  tool: string,
  args: Record<string, unknown>,
): Promise<ToolRun> {
  return once(run.world, pmId(run, code), tool, args);
}

/** The texts the GM received, oldest first. */
export async function gmInbox(run: Run): Promise<string[]> {
  return incoming(await messagesOf(run.world, run.gm));
}

/** The notices the GM received about PM `code`. */
export async function noticesFor(run: Run, code: string): Promise<string[]> {
  const head = `[coven notice] pm-${code} `;
  return (await gmInbox(run)).filter((text) => text.startsWith(head));
}

/** The tool calls of one PM session over its whole life. */
export async function pmHistory(run: Run, code: string): Promise<ToolRun[]> {
  return toolsIn(await messagesOf(run.world, pmId(run, code)));
}

/** The server's log (the plugin's undelivered notices and held-back lines go here). */
export async function serveLog(run: Run): Promise<string> {
  const { serveDir } = await import("./api.ts");
  return readFile(`${serveDir(run.world.profile)}/log`, "utf8").catch(() => "");
}

/** Waits `ms`: for a second copy of an event to show up if there is one. */
export const settleFor = (ms: number): Promise<void> => sleep(ms);

/** The raw session info, for time and outcome. */
export function info(run: Run, sessionID: string) {
  return infoOf(run.world, sessionID);
}
