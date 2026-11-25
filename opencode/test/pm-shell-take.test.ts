import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike, ShellCreateEvent } from "../src/context.ts";
import { ROLE_ENV } from "../src/pm/role.ts";
import { ShellQueue, shellEnvHooks } from "../src/pm/shell-env.ts";
import { sharedState } from "../src/pm/state.ts";

const KEY = "GATEWAY_KEY";
const PM_SESSION: SessionLike = {
  id: "ses_pm",
  location: { directory: "/w" },
  metadata: { coven: { role: "pm", code: "proj" } },
};

/** Two plugin copies: their own hooks over one queue and one set of taken events. */
function copies() {
  const queue = new ShellQueue();
  const taken = new WeakSet<object>();
  const make = () =>
    shellEnvHooks({
      keyEnv: KEY,
      directory: "/loc",
      queue,
      taken,
      now: () => 1_000_000,
      get: () => Promise.resolve(PM_SESSION),
    });
  return { first: make(), second: make() };
}

function shellEvent(env: Record<string, string>) {
  return {
    command: "ls",
    cwd: "/loc",
    timeout: 1000,
    shell: "/bin/sh",
    env,
  } satisfies ShellCreateEvent & Record<string, unknown>;
}

const call = {
  tool: "shell",
  sessionID: "ses_pm",
  agent: "build",
  messageID: "m",
  id: "c",
  input: { command: "ls" },
};

await test("two createBefore hooks on one event take one entry", async () => {
  const { first, second } = copies();
  await first.executeBefore(call);
  await first.executeBefore(call);
  const one = shellEvent({});
  first.createBefore(one);
  second.createBefore(one);
  assert.deepEqual(one.env, { [ROLE_ENV]: "pm" });
  const next = shellEvent({});
  second.createBefore(next);
  assert.deepEqual(next.env, { [ROLE_ENV]: "pm" });
  const last = shellEvent({});
  first.createBefore(last);
  assert.deepEqual(last.env, {});
});

await test("a second hook on the same event still scrubs", () => {
  const { first, second } = copies();
  const event = shellEvent({ [KEY]: "k" });
  first.createBefore(event);
  event.env[KEY] = "again";
  second.createBefore(event);
  assert.deepEqual(event.env, {});
});

await test("the shared state holds the taken events in a weak set", () => {
  assert.ok(sharedState().taken instanceof WeakSet);
});

await test("the shared state hangs on globalThis under a symbol key", () => {
  const state = sharedState();
  const holder = globalThis as Record<symbol, unknown>;
  assert.equal(holder[Symbol.for("coven.opencode.shared")], state);
});
