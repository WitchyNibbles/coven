/**
 * The script of the fake model: which reply comes next, decided from the conversation so far.
 * The first user message names the role.
 *   CHILD name=<n> sleep=<s>   one shell sleep, then "REPORT from <n>: ..."
 *   NEST name=<n> sleep=<s>    starts its own CHILD, reports "waiting", then reports "final"
 *   PARENT mode=<all|any|unknown> sleeps=<s,s,..> child=<CHILD|NEST>
 */
import { textOf } from "../harness/chat.ts";
import type { ChatMessage, Reply } from "../harness/chat.ts";
import type { Decide } from "../harness/fake-model.ts";
import { SESSION_ID, sessionIDsIn } from "../harness/history.ts";

interface ToolResult {
  name: string;
  text: string;
}

interface Conversation {
  messages: readonly ChatMessage[];
  toolNames: readonly string[];
  results: ToolResult[];
  params: Record<string, string>;
  /** Where the fake workers' shell commands run. */
  workdir: string;
}

const ROLE = /\b(CHILD|NEST|PARENT)\b/;
/** The `sessionID` of one `done` entry of an `await_agents` result. */
const FINISHED_ID = new RegExp(`"sessionID":"(${SESSION_ID.source})"`, "g");
const MAX_POLLS = 30;
const MAX_AWAITS = 6;

function parseParams(text: string): Record<string, string> {
  const entries = [...text.matchAll(/(\w+)=([\w,.-]+)/g)].map(
    (match) => [match[1] ?? "", match[2] ?? ""] as const,
  );
  return Object.fromEntries(entries);
}

function toolResults(messages: readonly ChatMessage[]): ToolResult[] {
  const names = new Map<string, string>();
  for (const message of messages) {
    for (const call of message.tool_calls ?? []) {
      names.set(call.id, call.function?.name ?? "");
    }
  }
  return messages
    .filter((message) => message.role === "tool")
    .map((message) => ({
      name: names.get(message.tool_call_id ?? "") ?? "",
      text: textOf(message.content),
    }));
}

function userTexts(messages: readonly ChatMessage[]): string[] {
  return messages
    .filter((message) => message.role === "user")
    .map((message) => textOf(message.content));
}

function reportsSeen(messages: readonly ChatMessage[]): string[] {
  return userTexts(messages).filter((text) => text.includes("<subagent"));
}

function shellName(toolNames: readonly string[]): string {
  return toolNames.includes("shell") ? "shell" : "bash";
}

/**
 * One shell sleep, run in `workdir`: the e2e project sits inside coven's checkout, where the
 * server guard allows only reading programs, so the sleep runs in a folder outside it.
 */
function sleepCall(
  toolNames: readonly string[],
  seconds: string,
  workdir: string,
): Reply {
  const args = {
    command: `sleep ${seconds}`,
    description: "work",
    timeout: 600000,
    workdir,
  };
  return { kind: "calls", calls: [{ name: shellName(toolNames), args }] };
}

function childReply(talk: Conversation): Reply {
  const name = talk.params["name"] ?? "?";
  const sleep = talk.params["sleep"] ?? "1";
  if (talk.results.some((r) => r.name === shellName(talk.toolNames))) {
    return { kind: "text", text: `REPORT from ${name}: slept ${sleep}s` };
  }
  return sleepCall(talk.toolNames, sleep, talk.workdir);
}

function nestReply(talk: Conversation): Reply {
  const name = talk.params["name"] ?? "?";
  if (talk.results.every((r) => r.name !== "subagent")) {
    const prompt = `CHILD name=${name}-inner sleep=${talk.params["sleep"] ?? "1"}`;
    const args = {
      agent: "worker",
      description: `inner of ${name}`,
      prompt,
    };
    return { kind: "calls", calls: [{ name: "subagent", args }] };
  }
  if (reportsSeen(talk.messages).length > 0) {
    return {
      kind: "text",
      text: `REPORT from ${name}: final after ${name}-inner`,
    };
  }
  return {
    kind: "text",
    text: `REPORT from ${name}: waiting for ${name}-inner`,
  };
}

function spawnCalls(talk: Conversation): Reply {
  const kind = talk.params["child"] ?? "CHILD";
  const sleeps = (talk.params["sleeps"] ?? "1").split(",");
  const calls = sleeps.map((sleep, index) => {
    const name = String.fromCharCode(65 + index);
    const prompt = `${kind} name=${name} sleep=${sleep}`;
    const args = { agent: "worker", description: `child ${name}`, prompt };
    return { name: "subagent", args };
  });
  return { kind: "calls", calls };
}

function spawnedIDs(talk: Conversation): string[] {
  const texts = talk.results
    .filter((r) => r.name === "subagent")
    .map((r) => r.text);
  return [...new Set(texts.flatMap(sessionIDsIn))];
}

/** The ids the lead's `await_agents` results listed under `done`. */
function finishedIDs(talk: Conversation): Set<string> {
  const ids = talk.results
    .filter((r) => r.name === "await_agents")
    .flatMap((r) => [...r.text.matchAll(FINISHED_ID)])
    .map((match) => match[1] ?? "");
  return new Set(ids);
}

function finalNeedle(talk: Conversation): string {
  return talk.params["child"] === "NEST" ? "final after" : "REPORT from";
}

/** True once every child's final report sits in the lead's history. */
function reportsComplete(talk: Conversation, count: number): boolean {
  const needle = finalNeedle(talk);
  const texts = reportsSeen(talk.messages).filter((t) => t.includes(needle));
  const ids = new Set(texts.flatMap(sessionIDsIn));
  return ids.size >= count;
}

function awaitCall(ids: string[], mode: string): Reply {
  return {
    kind: "calls",
    calls: [{ name: "await_agents", args: { ids, mode } }],
  };
}

function parentDone(talk: Conversation): Reply {
  const awaits = talk.results.filter((r) => r.name === "await_agents");
  const polls = talk.results.filter(
    (r) => r.name === shellName(talk.toolNames),
  );
  const count = (talk.params["sleeps"] ?? "").split(",").length;
  if (!reportsComplete(talk, count) && polls.length < MAX_POLLS) {
    return sleepCall(talk.toolNames, "1", talk.workdir);
  }
  const last = awaits.at(-1)?.text.slice(0, 400) ?? "";
  return {
    kind: "text",
    text: `PARENT DONE (${awaits.length} awaits): ${last}`,
  };
}

function parentReply(talk: Conversation): Reply {
  const mode = talk.params["mode"] ?? "all";
  if (mode === "unknown") {
    const awaited = talk.results.some((r) => r.name === "await_agents");
    return awaited
      ? { kind: "text", text: "PARENT DONE (unknown id)" }
      : awaitCall(["ses_doesnotexist"], "all");
  }
  const ids = spawnedIDs(talk);
  if (ids.length === 0) {
    return spawnCalls(talk);
  }
  if (
    talk.results.filter((r) => r.name === "await_agents").length > MAX_AWAITS
  ) {
    return {
      kind: "text",
      text: "PARENT GAVE UP: await_agents did not settle",
    };
  }
  const finished = finishedIDs(talk);
  const left = ids.filter((id) => !finished.has(id));
  const needsAwait = mode === "all" ? finished.size === 0 : left.length > 0;
  return needsAwait
    ? awaitCall(mode === "all" ? ids : left, mode)
    : parentDone(talk);
}

/** The fake model's script, with every shell command it asks for run in `workdir`. */
export function decideIn(workdir: string): Decide {
  return (messages, toolNames) => {
    const first = userTexts(messages).find((text) => ROLE.test(text)) ?? "";
    const role = ROLE.exec(first)?.[1];
    const talk: Conversation = {
      messages,
      toolNames,
      results: toolResults(messages),
      params: parseParams(first.slice(first.search(ROLE))),
      workdir,
    };
    if (role === "CHILD") {
      return childReply(talk);
    }
    if (role === "NEST") {
      return nestReply(talk);
    }
    return role === "PARENT" ? parentReply(talk) : { kind: "text", text: "ok" };
  };
}
