import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  ExecuteBeforeEvent,
  SessionLike,
  ShellCreateEvent,
} from "../src/context.ts";
import { ROLE_ENV, resolveRole } from "../src/pm/role.ts";
import { ShellQueue, shellEnvHooks } from "../src/pm/shell-env.ts";

const KEY = "GATEWAY_KEY";
const PASSWORD = "OPENCODE_SERVER_PASSWORD";

function shellEvent(
  env: Record<string, string>,
  command = "ls",
  cwd = "/work",
) {
  return {
    command,
    cwd,
    timeout: 1000,
    shell: "/bin/sh",
    env,
  } satisfies ShellCreateEvent & Record<string, unknown>;
}

function execEvent(
  sessionID: string,
  input: unknown,
  tool = "shell",
): Parameters<ReturnType<typeof shellEnvHooks>["executeBefore"]>[0] {
  return { tool, sessionID, agent: "build", messageID: "m", id: "c", input };
}

function world(sessions: Record<string, SessionLike | Error> = {}) {
  const clock = { now: 1_000_000 };
  const queue = new ShellQueue();
  const hooks = shellEnvHooks({
    keyEnv: KEY,
    directory: "/loc",
    queue,
    taken: new WeakSet(),
    now: () => clock.now,
  });
  const get = (id: string) => {
    const found = sessions[id];
    return found instanceof Error
      ? Promise.reject(found)
      : Promise.resolve(found);
  };
  return {
    clock,
    queue,
    ...hooks,
    // the plugin hands the hook the call's one role lookup; here it is made the same way
    executeBefore: (event: ExecuteBeforeEvent) =>
      hooks.executeBefore(event, () => resolveRole(event.sessionID, get)),
  };
}

const PM_SESSION: SessionLike = {
  id: "ses_pm",
  location: { directory: "/w" },
  metadata: { coven: { role: "pm", code: "proj" } },
};
const GM_SESSION: SessionLike = {
  id: "ses_gm",
  location: { directory: "/w" },
  metadata: { coven: { role: "gm" } },
};
const PLAIN_SESSION: SessionLike = {
  id: "ses_plain",
  location: { directory: "/w" },
};

await test("createBefore sets ROLE_ENV for a pm shell and scrubs first", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls", workdir: "/w" }));
  const event = shellEvent(
    { [KEY]: "k", [PASSWORD]: "p", PATH: "/bin" },
    "ls",
    "/w",
  );
  w.createBefore(event);
  assert.deepEqual(event.env, { PATH: "/bin", [ROLE_ENV]: "pm" });
});

await test("a scrub that throws makes createBefore throw", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }));
  const env = {};
  Object.defineProperty(env, KEY, {
    value: "k",
    enumerable: true,
    configurable: false,
  });
  assert.throws(() => w.createBefore(shellEvent(env, "ls", "/loc")), TypeError);
});

await test("a gm shell gets no ROLE_ENV", async () => {
  const w = world({ ses_gm: GM_SESSION });
  await w.executeBefore(execEvent("ses_gm", { command: "ls" }));
  const event = shellEvent({ PATH: "/bin", [KEY]: "k" }, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("a shell of a session with no role gets no ROLE_ENV", async () => {
  const w = world({ ses_plain: PLAIN_SESSION });
  await w.executeBefore(execEvent("ses_plain", { command: "ls" }));
  const event = shellEvent({ PATH: "/bin" }, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("a shell whose role lookup fails gets ROLE_ENV", async () => {
  const w = world({ ses_x: new Error("down") });
  await w.executeBefore(execEvent("ses_x", { command: "ls" }));
  const event = shellEvent({}, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, { [ROLE_ENV]: "pm" });
});

await test("a shell with no workdir pairs on the location directory", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }));
  const event = shellEvent({}, "ls", "/loc");
  w.createBefore(event);
  assert.equal(event.env[ROLE_ENV], "pm");
});

await test("a relative workdir pairs by the string the call gave", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls", workdir: "sub" }));
  const event = shellEvent({}, "ls", "sub");
  w.createBefore(event);
  assert.equal(event.env[ROLE_ENV], "pm");
});

await test("a shell with no recorded call gets no ROLE_ENV", () => {
  const w = world({});
  const event = shellEvent({ PATH: "/bin" }, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("a recorded call expires before its shell starts", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }));
  w.clock.now += 30_000;
  const event = shellEvent({}, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, {});
});

await test("a pm and a gm running one command in one folder both get ROLE_ENV", async () => {
  const w = world({ ses_pm: PM_SESSION, ses_gm: GM_SESSION });
  await w.executeBefore(execEvent("ses_gm", { command: "ls" }));
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }));
  const first = shellEvent({}, "ls", "/loc");
  w.createBefore(first);
  const second = shellEvent({}, "ls", "/loc");
  w.createBefore(second);
  assert.equal(first.env[ROLE_ENV], "pm");
  assert.equal(second.env[ROLE_ENV], "pm");
});

await test("executeBefore records the bash tool too", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }, "bash"));
  const event = shellEvent({}, "ls", "/loc");
  w.createBefore(event);
  assert.equal(event.env[ROLE_ENV], "pm");
});

await test("executeBefore ignores tools that are not a shell", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: "ls" }, "read"));
  const event = shellEvent({}, "ls", "/loc");
  w.createBefore(event);
  assert.deepEqual(event.env, {});
});

await test("executeBefore ignores a shell call with no command string", async () => {
  const w = world({ ses_pm: PM_SESSION });
  await w.executeBefore(execEvent("ses_pm", { command: 5 }));
  await w.executeBefore(execEvent("ses_pm", "ls"));
  await w.executeBefore(execEvent("ses_pm", null));
  assert.equal(w.queue.take("ls", "/loc", 1_000_001), undefined);
});

await test("a role lookup that throws never makes executeBefore throw", async () => {
  const w = world({ ses_x: new Error("down") });
  await w.executeBefore(execEvent("ses_x", { command: "ls" }));
  assert.equal(w.queue.take("ls", "/loc", 1_000_001), "pm");
});
