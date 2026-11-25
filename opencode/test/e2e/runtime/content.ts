/** The content checks: deny-secrets through the plugin and through the config, and the SDD smoke. */
import { cp, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import { startFakeModel } from "../../harness/fake-model.ts";
import type { RecordedRequest } from "../../harness/fake-model.ts";
import { withServer } from "../../harness/server.ts";
import type { Api } from "../../harness/server.ts";
import {
  awaitResults,
  exportedMessages,
  SESSION_ID,
} from "../../harness/history.ts";
import type { ToolRun } from "../../harness/history.ts";
import { allowShell, configPath, pointProviderAt } from "./profile.ts";
import type { RuntimeWorld } from "./profile.ts";
import {
  decideRuntime,
  KEY_VALUE,
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
  return exportedMessages(exported).map((message) => JSON.stringify(message));
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
  return exportedMessages(exported)
    .filter((message) => message.type === "assistant")
    .map((message) => message.agent ?? "(none)");
}

/** The agent a `subagent` call named, if any. */
function agentOf(call: ToolRun | undefined): string | undefined {
  return (call?.input as { agent?: string } | undefined)?.agent;
}

/** What the `subagent` call was, for a message: its status and the agent it named. */
function describeCall(call: ToolRun | undefined): string {
  return `${call?.status ?? "missing"} for agent ${agentOf(call)}`;
}

/** The session id of a completed `subagent` call for `role`, or the reason it is not one. */
function startedChild(
  call: ToolRun | undefined,
  role: string,
): { id: string } | { problem: string } {
  if (call?.status !== "completed" || agentOf(call) !== role) {
    return { problem: `the ${role} subagent call was ${describeCall(call)}` };
  }
  const id = SESSION_ID.exec(call.output)?.[0];
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

interface Seen {
  line: string;
  requests: readonly RecordedRequest[];
}

const PRESENCE_LINE = /KEY=\[(set)?\] PW=\[(set)?\]/;

/** The presence line a shell call printed, or the reason there is none. */
function presenceOf(session: Session): string {
  const run = session.history.tools.find((tool) => tool.name === "shell");
  if (run === undefined) {
    return `no shell call ran; the reply was: ${session.history.finalText.slice(0, 120)}`;
  }
  return (
    PRESENCE_LINE.exec(run.output)?.[0] ??
    `no presence line in ${run.status} ${run.output.slice(0, 120)}`
  );
}

/** What is wrong with the provider's authentication in `requests`, or undefined when each had the key. */
function authProblem(requests: readonly RecordedRequest[]): string | undefined {
  const expected = `Bearer ${KEY_VALUE}`;
  const wrong = requests.filter(
    (request) => request.authorization !== expected,
  );
  // Two requests at least: one before the shell call and one after it.
  if (requests.length < 2 || wrong.length > 0) {
    return `${wrong.length} of ${requests.length} model requests lacked the provider key`;
  }
  return undefined;
}

const SERVED_TIMEOUT_MS = 60_000;
const SERVED_POLL_MS = 250;
const SERVED_DONE = "SHELLENV DONE";

/** Reads the session's messages until the script's last words are in them, or the time is up. */
async function servedMessages(api: Api, id: string): Promise<string> {
  const limit = Date.now() + SERVED_TIMEOUT_MS;
  for (;;) {
    const text = JSON.stringify(await api.get(`/api/session/${id}/message`));
    if (text.includes(SERVED_DONE) || Date.now() > limit) {
      return text;
    }
    await delay(SERVED_POLL_MS);
  }
}

/** The shell's presence line and the model requests of a session run in a served OpenCode. */
async function servedSession(world: RuntimeWorld): Promise<Seen> {
  const before = world.model.requests().length;
  const options = { cwd: world.work, env: world.env };
  const messages = await withServer(options, async (api) => {
    const created = await api.post("/api/session", {
      location: { directory: world.work },
      title: "shell-env",
    });
    const id = SESSION_ID.exec(JSON.stringify(created))?.[0];
    if (id === undefined) {
      throw new Error("the server returned no session id");
    }
    await api.post(`/api/session/${id}/prompt`, { text: "SHELLENV" });
    return servedMessages(api, id);
  });
  return {
    line: PRESENCE_LINE.exec(messages)?.[0] ?? "(no presence line)",
    requests: world.model.requests().slice(before),
  };
}

/** The shell's presence line and the model requests of a `run --standalone` session. */
async function standaloneSession(world: RuntimeWorld): Promise<Seen> {
  const session = await runPrompt(world, "SHELLENV");
  return { line: presenceOf(session), requests: session.requests };
}

/**
 * A shell command the model runs must see neither the provider key nor the server password,
 * in a standalone run and in a served session, while the provider still gets the key.
 */
export async function shellEnvProblem(
  world: RuntimeWorld,
): Promise<string | undefined> {
  const config = await readFile(configPath(world.profile), "utf8");
  try {
    await allowShell(world.profile);
    const problems: string[] = [];
    for (const [name, run] of [
      ["standalone", standaloneSession],
      ["served", servedSession],
    ] as const) {
      const seen = await run(world);
      const problem =
        seen.line === "KEY=[] PW=[]"
          ? authProblem(seen.requests)
          : `the shell saw: ${seen.line}`;
      if (problem !== undefined) {
        problems.push(`${name}: ${problem}`);
      }
    }
    return problems.length === 0 ? undefined : problems.join("; ");
  } finally {
    await writeFile(configPath(world.profile), config);
  }
}
