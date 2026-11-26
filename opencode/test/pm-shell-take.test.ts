import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  ExecuteBeforeEvent,
  SessionLike,
  ShellCreateEvent,
} from "../src/context.ts";
import { ROLE_ENV, resolveRole } from "../src/pm/role.ts";
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
  const make = () => {
    const hooks = shellEnvHooks({
      keyEnv: KEY,
      directory: "/loc",
      queue,
      taken,
      now: () => 1_000_000,
    });
    return {
      ...hooks,
      // the plugin hands the hook the call's one role lookup; here it is made the same way
      executeBefore: (event: ExecuteBeforeEvent) =>
        hooks.executeBefore(event, () =>
          resolveRole(event.sessionID, () => Promise.resolve(PM_SESSION)),
        ),
    };
  };
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

function queueWith(roles: ("pm" | "gm")[]): ShellQueue {
  const queue = new ShellQueue();
  roles.forEach((role, index) => {
    queue.record({ role, command: "ls", cwd: "/w" }, 1000 + index);
  });
  return queue;
}

await test("a pm recorded before a gm gets pm on both takes", () => {
  const queue = queueWith(["pm", "gm"]);
  assert.equal(queue.take("ls", "/w", 1010), "pm");
  assert.equal(queue.take("ls", "/w", 1011), "pm");
});

await test("a gm recorded before a pm gets pm on both takes", () => {
  const queue = queueWith(["gm", "pm"]);
  assert.equal(queue.take("ls", "/w", 1010), "pm");
  assert.equal(queue.take("ls", "/w", 1011), "pm");
});

await test("a mixed key is empty after the takes that match its calls", () => {
  const queue = queueWith(["pm", "gm"]);
  queue.take("ls", "/w", 1010);
  queue.take("ls", "/w", 1011);
  assert.equal(queue.take("ls", "/w", 1012), undefined);
});

await test("a gm shell that starts first leaves the pm entry for the pm shell", () => {
  const queue = queueWith(["gm", "pm"]);
  queue.take("ls", "/w", 1010);
  assert.equal(queue.take("ls", "/w", 1011), "pm");
});

await test("an empty workdir pairs on the location directory, as the guard reads it", async () => {
  const { first } = copies();
  await first.executeBefore({ ...call, input: { command: "ls", workdir: "" } });
  const event = shellEvent({});
  first.createBefore(event);
  assert.deepEqual(event.env, { [ROLE_ENV]: "pm" });
});

await test("an empty workdir also pairs with a shell that starts with an empty cwd", async () => {
  const { first } = copies();
  await first.executeBefore({ ...call, input: { command: "ls", workdir: "" } });
  const event = { ...shellEvent({}), cwd: "" };
  first.createBefore(event);
  assert.deepEqual(event.env, { [ROLE_ENV]: "pm" });
});

await test("an empty workdir call is taken once, whichever form its shell has", async () => {
  const { first } = copies();
  await first.executeBefore({ ...call, input: { command: "ls", workdir: "" } });
  first.createBefore({ ...shellEvent({}), cwd: "" });
  const other = shellEvent({});
  first.createBefore(other);
  assert.deepEqual(other.env, {});
  await first.executeBefore({ ...call, input: { command: "ls", workdir: "" } });
  first.createBefore(shellEvent({}));
  const left = { ...shellEvent({}), cwd: "" };
  first.createBefore(left);
  assert.deepEqual(left.env, {});
});
