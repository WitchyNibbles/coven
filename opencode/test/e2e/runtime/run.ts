/** One OpenCode session through `coven opencode run`. */
import { coven } from "../../harness/coven.ts";
import { readHistory } from "../../harness/history.ts";
import type { LeadHistory } from "../../harness/history.ts";
import type { RecordedRequest } from "../../harness/fake-model.ts";
import type { RuntimeWorld } from "./profile.ts";

const SESSION_TIMEOUT_MS = 2 * 60 * 1000;

/** What a session left: the lead's history and the requests the fake saw meanwhile. */
export interface Session {
  history: LeadHistory;
  requests: readonly RecordedRequest[];
}

function sessionOf(out: string): string {
  const first = out.split("\n").find((line) => line.startsWith("{")) ?? "{}";
  const id = (JSON.parse(first) as { sessionID?: string }).sessionID;
  if (id === undefined) {
    throw new Error(`the run printed no session id: ${out.slice(0, 300)}`);
  }
  return id;
}

/** `coven opencode run -- <args>` in the work folder, to its end; throws on a non-zero exit. */
async function opencode(world: RuntimeWorld, args: string[]): Promise<string> {
  const options = {
    cwd: world.work,
    env: world.env,
    timeoutMs: SESSION_TIMEOUT_MS,
  };
  const done = await coven(["opencode", "run", "--", ...args], options);
  if (done.code !== 0) {
    const text = (done.err || done.out).slice(-400);
    throw new Error(`opencode ${args[0]} exited ${done.code}: ${text}`);
  }
  return done.out;
}

/** Runs the PING prompt, exports the session and reads the requests it caused. */
export async function runSession(world: RuntimeWorld): Promise<Session> {
  const before = world.model.requests().length;
  const out = await opencode(world, [
    "run",
    "--standalone",
    "--format",
    "json",
    "PING",
  ]);
  const exported = await opencode(world, [
    "session",
    "export",
    "--standalone",
    sessionOf(out),
  ]);
  return {
    history: readHistory(exported),
    requests: world.model.requests().slice(before),
  };
}
