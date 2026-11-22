/** The content checks: deny-secrets through the plugin and through the config, and the SDD smoke. */
import { cp, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import { startFakeModel } from "../../harness/fake-model.ts";
import { awaitResults } from "../../harness/history.ts";
import type { ToolRun } from "../../harness/history.ts";
import { configPath, pointProviderAt } from "./profile.ts";
import type { RuntimeWorld } from "./profile.ts";
import {
  decideRuntime,
  reportOf,
  ROLES,
  SECRET_VALUE,
} from "./content-script.ts";
import { exportSession, runPrompt } from "./run.ts";
import type { Session } from "./run.ts";

const REFUSAL = "coven deny-secrets:";
/** What OpenCode throws for a call its config denies; its own ask-rule says something else. */
const CONFIG_DENIAL = "Permission denied: read";

/** What is wrong with how `session` ended the `name` call, or undefined when it was refused. */
function refusedProblem(
  session: Session,
  name: string,
  by: "plugin" | "config",
): string | undefined {
  const run = session.history.tools.find((tool) => tool.name === name);
  if (run === undefined) {
    return `no ${name} call ran; the reply was: ${session.history.finalText.slice(0, 120)}`;
  }
  const text = run.output;
  const right =
    by === "plugin"
      ? text.includes(REFUSAL)
      : text.includes(CONFIG_DENIAL) && !text.includes(REFUSAL);
  if (run.status === "completed" || !right) {
    return `${name} was ${run.status} with ${run.output.slice(0, 160)}`;
  }
  return undefined;
}

function leakProblem(session: Session): string | undefined {
  return session.exported.includes(SECRET_VALUE)
    ? "the secret value is in the session export"
    : undefined;
}

/** Plugin and config denies hold together, then the config deny holds alone. */
export async function secretsProblem(
  world: RuntimeWorld,
): Promise<string | undefined> {
  const both = await runPrompt(world, "SECRETS");
  const first =
    refusedProblem(both, "read", "plugin") ??
    refusedProblem(both, "shell", "plugin") ??
    leakProblem(both);
  if (first !== undefined) {
    return `with the plugin: ${first}`;
  }
  const alone = await withoutPlugin(world, (bare) =>
    runPrompt(bare, "SECRETS_READ"),
  );
  const second = refusedProblem(alone, "read", "config") ?? leakProblem(alone);
  return second === undefined ? undefined : `without the plugin: ${second}`;
}

/** Runs `work` with the plugin folder gone and the provider on a plain fake; restores both. */
async function withoutPlugin<T>(
  world: RuntimeWorld,
  work: (bare: RuntimeWorld) => Promise<T>,
): Promise<T> {
  const plugin = join(world.profile, "plugin");
  const backup = join(dirname(world.profile), "plugin-backup");
  const config = await readFile(configPath(world.profile), "utf8");
  const plain = await startFakeModel({
    decide: decideRuntime,
    mode: "plain",
    port: 0,
  });
  try {
    await cp(plugin, backup, { recursive: true });
    await rm(plugin, { recursive: true, force: true });
    await pointProviderAt(world.profile, plain.port);
    return await work({ ...world, model: plain });
  } finally {
    await plain.close();
    await rm(plugin, { recursive: true, force: true });
    await cp(backup, plugin, { recursive: true });
    await writeFile(configPath(world.profile), config);
  }
}

function count(text: string, needle: string): number {
  return text.split(needle).length - 1;
}

/** The exported messages as raw JSON strings, in the order the lead's history holds them. */
function messageTexts(exported: string): string[] {
  const { messages = [] } = JSON.parse(exported) as { messages?: unknown[] };
  return messages.map((message) => JSON.stringify(message));
}

/** What is wrong with where the `role` report sits in the history, or undefined. */
function reportProblem(
  texts: readonly string[],
  role: string,
  awaitAt: number,
): string | undefined {
  const marker = reportOf(role);
  const seen = texts.reduce((sum, text) => sum + count(text, marker), 0);
  if (seen !== 1) {
    return `the ${role} report is in the history ${seen} times`;
  }
  return texts.findIndex((text) => text.includes(marker)) > awaitAt
    ? undefined
    : `the ${role} report came before the await_agents result`;
}

/** The agent ids the assistant messages of an exported session ran as. */
function agentsOf(exported: string): string[] {
  const { messages = [] } = JSON.parse(exported) as {
    messages?: { type?: string; agent?: string }[];
  };
  return messages
    .filter((message) => message.type === "assistant")
    .map((message) => message.agent ?? "(none)");
}

/** What the `subagent` call was, for a message: its status and the agent it named. */
function describeCall(call: ToolRun | undefined): string {
  const input = call?.input as { agent?: string } | undefined;
  return `${call?.status ?? "missing"} for agent ${input?.agent}`;
}

/** The session id of a completed `subagent` call for `role`, or the reason it is not one. */
function startedChild(
  call: ToolRun | undefined,
  role: string,
): { id: string } | { problem: string } {
  const input = call?.input as { agent?: string } | undefined;
  if (call?.status !== "completed" || input?.agent !== role) {
    return { problem: `the ${role} subagent call was ${describeCall(call)}` };
  }
  const id = /ses_[A-Za-z0-9_]+/.exec(call.output)?.[0];
  return id === undefined
    ? { problem: `the ${role} subagent call returned no session id` }
    : { id };
}

/**
 * What is wrong with how the `subagent` call for `role` ran: it must have completed with that
 * agent, and the child session it started must have run as that agent (read from the child's
 * own export), so a fallback agent cannot pass.
 */
async function childProblem(
  world: RuntimeWorld,
  call: ToolRun | undefined,
  role: string,
): Promise<string | undefined> {
  const child = startedChild(call, role);
  if ("problem" in child) {
    return child.problem;
  }
  const agents = agentsOf(await exportSession(world, child.id));
  return agents.length > 0 && agents.every((agent) => agent === role)
    ? undefined
    : `the ${role} child ran as ${agents.join(", ") || "no agent"}`;
}

/** What is wrong with the SDD session, where both roles are awaited together, or undefined. */
export async function sddProblem(
  world: RuntimeWorld,
): Promise<string | undefined> {
  const session = await runPrompt(world, "SDD_LEAD");
  const started = session.history.tools.filter((t) => t.name === "subagent");
  const awaited = awaitResults(session.history);
  const [only] = awaited;
  if (started.length !== ROLES.length || awaited.length !== 1 || !only) {
    return `${started.length} subagent calls, ${awaited.length} await_agents results`;
  }
  if (only.mode !== "all" || only.done.length !== ROLES.length) {
    return `await_agents returned ${JSON.stringify(only).slice(0, 200)}`;
  }
  for (const [index, role] of ROLES.entries()) {
    const problem = await childProblem(world, started[index], role);
    if (problem !== undefined) {
      return problem;
    }
  }
  const texts = messageTexts(session.exported);
  // The one await_agents result is guaranteed by the guard above, so the index is never -1.
  const awaitAt = texts.findIndex((text) => text.includes('"await_agents"'));
  return ROLES.map((role) => reportProblem(texts, role, awaitAt)).find(
    (problem) => problem !== undefined,
  );
}
