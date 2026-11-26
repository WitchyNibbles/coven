import assert from "node:assert/strict";
import { basename } from "node:path";
import { setImmediate as tick } from "node:timers/promises";

import type { ExecuteBeforeEvent } from "../src/context.ts";
import { exits } from "./guard-fake.ts";
import type { Behaviour, SpawnCall } from "./guard-fake.ts";
import { PROFILE_OPTIONS, bootCoven } from "./plugin-fixture.ts";

/** The helpers of the tests that drive the plugin's one `execute.before` hook. */

export const COVEN = {
  covenRoot: "/c",
  keyEnv: "K",
  ...PROFILE_OPTIONS,
  toolChoiceAuto: [],
};

export const SERVER = "opencode_server_guard.py";
export const SCRIPTS_FOR_PM = [
  "deny_secrets.py",
  SERVER,
  "pm_permissions.py",
  "prod_guard.py",
];
export const SCRIPTS_FOR_GM = ["deny_secrets.py", SERVER];

export function toolEvent(
  sessionID: string,
  input: unknown = { command: "ls" },
  tool = "shell",
): ExecuteBeforeEvent {
  return {
    tool,
    sessionID,
    agent: "build",
    messageID: "msg_1",
    id: "call_1",
    input,
  };
}

export const cleanups: (() => void)[] = [];

/** The coven plugin booted with its one `execute.before` hook; `guard` acts per hook script. */
export async function booted(setup: Parameters<typeof bootCoven>[0] = {}) {
  const p = bootCoven({ options: COVEN, ...setup });
  cleanups.push(await p.outcome);
  const hook = p.toolHooks.find((entry) => entry.name === "execute.before");
  assert.ok(hook !== undefined);
  return {
    p,
    run: async (event: ExecuteBeforeEvent): Promise<void> => {
      await hook.callback(event);
    },
  };
}

/** Hook children, by script name, in the order they started. */
export function scripts(p: { spawned: readonly SpawnCall[] }): string[] {
  return p.spawned.map((call) => basename(call.args[1] ?? ""));
}

/** A guard whose children act by script; the others allow. */
export function perScript(acts: Record<string, Behaviour>): Behaviour {
  return (child) => {
    (acts[basename(child.call.args[1] ?? "")] ?? exits(0))(child);
  };
}

/** Lets the sequential hook children start: each one spawns a tick after the last one ends. */
export async function settleStages(): Promise<void> {
  for (let i = 0; i < 12; i += 1) {
    await tick();
  }
}
