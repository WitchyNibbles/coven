/** Sessions of the served world: make one, send it directives, wait for the turn and read what it did. */
import { at, stringOf } from "./api.ts";
import type { Answer } from "./api.ts";
import { doLine } from "./script.ts";
import type { HierarchyWorld } from "./world.ts";

const TURN_TIMEOUT_MS = 60_000;
const POLL_MS = 100;

export interface Rule {
  action: string;
  resource: string;
  effect: "allow" | "deny" | "ask";
}

export interface NewSession {
  directory: string;
  agent?: string;
  title?: string;
  metadata?: Record<string, unknown>;
  permissions?: readonly Rule[];
  parentID?: string;
}

/** One tool call of a session's history, with what it returned or the error it came back with. */
export interface ToolRun {
  name: string;
  status: string;
  input: unknown;
  text: string;
}

export const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

function bodyOf(answer: Answer, what: string): Record<string, unknown> {
  const data = at(answer.body, "data");
  if (answer.status !== 200 || typeof data !== "object" || data === null) {
    throw new Error(
      `${what}: HTTP ${answer.status} ${JSON.stringify(answer.body).slice(0, 200)}`,
    );
  }
  return data as Record<string, unknown>;
}

/** Makes a session over the API and returns its id. */
export async function createSession(
  world: HierarchyWorld,
  made: NewSession,
): Promise<string> {
  const { directory, ...rest } = made;
  const answer = await world.api.post("/api/session", {
    ...rest,
    location: { directory },
  });
  return String(bodyOf(answer, "create session")["id"]);
}

/** `GET /api/session/{id}`: the session as the server reports it. */
export async function infoOf(
  world: HierarchyWorld,
  sessionID: string,
): Promise<Record<string, unknown>> {
  return bodyOf(
    await world.api.get(`/api/session/${sessionID}`),
    `get ${sessionID}`,
  );
}

/** The `time.idle` of a session, or 0 when it has none yet. */
async function idleOf(
  world: HierarchyWorld,
  sessionID: string,
): Promise<number> {
  const idle = at(await infoOf(world, sessionID), "time", "idle");
  return typeof idle === "number" ? idle : 0;
}

/** Sends `text` to the session; returns the answer without waiting for the turn. */
export function send(
  world: HierarchyWorld,
  sessionID: string,
  text: string,
  delivery?: "queue" | "steer",
): Promise<Answer> {
  return world.api.post(`/api/session/${sessionID}/prompt`, {
    text,
    ...(delivery === undefined ? {} : { delivery }),
  });
}

/** Waits until the session's `time.idle` is later than `since`; throws after a minute. */
export async function waitIdleAfter(
  world: HierarchyWorld,
  sessionID: string,
  since: number,
  timeoutMs = TURN_TIMEOUT_MS,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while ((await idleOf(world, sessionID)) <= since) {
    if (Date.now() > deadline) {
      throw new Error(
        `session ${sessionID} had not gone idle within ${timeoutMs / 1000} s`,
      );
    }
    await sleep(POLL_MS);
  }
}

/** Every message of the session, oldest first. */
export async function messagesOf(
  world: HierarchyWorld,
  sessionID: string,
): Promise<Record<string, unknown>[]> {
  const answer = await world.api.get(`/api/session/${sessionID}/context`);
  const data = at(answer.body, "data");
  if (answer.status !== 200 || !Array.isArray(data)) {
    throw new Error(`context of ${sessionID}: HTTP ${answer.status}`);
  }
  return data as Record<string, unknown>[];
}

function textOfPart(part: Record<string, unknown>): string {
  const state = part["state"];
  const content = at(state, "content");
  if (Array.isArray(content)) {
    return content.map((piece) => stringOf(at(piece, "text"))).join("\n");
  }
  const message = at(state, "error", "message");
  return typeof message === "string"
    ? message
    : JSON.stringify(at(state, "error") ?? "");
}

/** The tool calls in `messages`, in order. */
export function toolsIn(
  messages: readonly Record<string, unknown>[],
): ToolRun[] {
  return messages
    .filter((message) => message["type"] === "assistant")
    .flatMap(
      (message) =>
        (message["content"] as Record<string, unknown>[] | undefined) ?? [],
    )
    .filter((part) => part["type"] === "tool")
    .map((part) => ({
      name: String(part["name"]),
      status: String(at(part, "state", "status")),
      input: at(part, "state", "input"),
      text: textOfPart(part),
    }));
}

/** The texts of the user and synthetic messages, oldest first. */
export function incoming(
  messages: readonly Record<string, unknown>[],
): string[] {
  return messages
    .filter(
      (message) =>
        message["type"] === "user" || message["type"] === "synthetic",
    )
    .map((message) => stringOf(message["text"]));
}

/** The text of the last assistant message that has any. */
export function lastText(messages: readonly Record<string, unknown>[]): string {
  const texts = messages
    .filter((message) => message["type"] === "assistant")
    .map((message) =>
      ((message["content"] as Record<string, unknown>[] | undefined) ?? [])
        .filter((part) => part["type"] === "text")
        .map((part) => stringOf(part["text"]))
        .join("\n"),
    )
    .filter((text) => text !== "");
  return texts.at(-1) ?? "";
}

/** True while the session's turn runs: it turned busy and has not gone idle since. */
export function isBusyInfo(info: Record<string, unknown>): boolean {
  const busy = at(info, "time", "busy");
  const idle = at(info, "time", "idle");
  return typeof busy === "number" && (typeof idle !== "number" || idle < busy);
}

/**
 * Waits until the session is idle and has not changed for `stableMs`, so a turn a queued message
 * started a moment ago is over before the next prompt goes in. Throws after a minute.
 */
export async function quiet(
  world: HierarchyWorld,
  sessionID: string,
  stableMs = 1200,
): Promise<void> {
  const deadline = Date.now() + TURN_TIMEOUT_MS;
  let seen = "";
  let since = Date.now();
  while (Date.now() < deadline) {
    const info = await infoOf(world, sessionID);
    const mark = `${String(at(info, "time", "updated"))}/${isBusyInfo(info)}`;
    if (mark !== seen) {
      seen = mark;
      since = Date.now();
    } else if (!isBusyInfo(info) && Date.now() - since >= stableMs) {
      return;
    }
    await sleep(POLL_MS);
  }
  throw new Error(`session ${sessionID} did not settle within a minute`);
}

/** Sends the directives, waits for the turn to end and returns the tool calls that turn made. */
export async function turn(
  world: HierarchyWorld,
  sessionID: string,
  calls: readonly [string, Record<string, unknown>][],
  delivery?: "queue" | "steer",
): Promise<ToolRun[]> {
  await quiet(world, sessionID);
  const before = (await messagesOf(world, sessionID)).length;
  const since = await idleOf(world, sessionID);
  const text = calls.map(([tool, args]) => doLine(tool, args)).join("\n");
  const answer = await send(world, sessionID, text, delivery);
  bodyOf(answer, "prompt");
  await waitIdleAfter(world, sessionID, since);
  return toolsIn((await messagesOf(world, sessionID)).slice(before));
}

/** The one tool run of a one-call turn; throws when the turn made another number. */
export async function once(
  world: HierarchyWorld,
  sessionID: string,
  tool: string,
  args: Record<string, unknown>,
): Promise<ToolRun> {
  const runs = await turn(world, sessionID, [[tool, args]]);
  const [only] = runs;
  if (only === undefined || runs.length !== 1) {
    throw new Error(`${tool} made ${runs.length} tool calls, not 1`);
  }
  return only;
}
