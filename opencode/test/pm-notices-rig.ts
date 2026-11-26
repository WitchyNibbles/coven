import type { SessionLike } from "../src/context.ts";
import { NoticeTracker } from "../src/pm/notices.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, gmWorld } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

export const HERE = "/work/repo";
export const PM: SessionLike = {
  id: "pm1",
  location: { directory: HERE },
  metadata: coven("pm", "acme"),
};

export interface Rig {
  world: World;
  tracker: NoticeTracker;
  state: { messaged: Set<string>; stopped: Set<string> };
  logs: string[];
}

export function rig(
  extra: readonly SessionLike[] = [],
  text: () => Promise<string> = () => Promise.resolve("working\nall done\n"),
  directory = HERE,
  startedAt = 0,
): Rig {
  const world = gmWorld([GM, PM, ...extra]);
  const state = { messaged: new Set<string>(), stopped: new Set<string>() };
  const logs: string[] = [];
  const tracker = new NoticeTracker(directory, {
    session: world,
    state,
    lastText: text,
    startedAt,
    log: (line) => logs.push(line),
  });
  return { world, tracker, state, logs };
}

export function end(
  sessionID: string,
  outcome: "succeeded" | "failed" | "interrupted" = "succeeded",
): unknown {
  return { type: `session.execution.${outcome}`, data: { sessionID } };
}

export function start(sessionID: string): unknown {
  return { type: "session.execution.started", data: { sessionID } };
}

export function sent(world: World): string[] {
  return world.prompts.map((each) => each.text);
}
