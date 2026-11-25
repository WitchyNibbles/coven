import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike, ShellCreateEvent } from "../src/context.ts";
import { ROLE_ENV } from "../src/pm/role.ts";
import type { Role } from "../src/pm/role.ts";
import { ShellQueue, scrubEnv, shellEnvHooks } from "../src/pm/shell-env.ts";
import { sharedState } from "../src/pm/state.ts";

const KEY = "GATEWAY_KEY";
const PASSWORD = "OPENCODE_SERVER_PASSWORD";

function entry(
  role: Role | undefined,
  command = "ls",
  cwd = "/work",
  sessionID = "ses_1",
) {
  return { sessionID, role, command, cwd };
}

/** A fresh evaluation of `state.ts`, as 2.3.2 makes one per location. */
async function stateCopy(copy: number): Promise<{
  sharedState: typeof sharedState;
}> {
  const url = new URL("../src/pm/state.ts", import.meta.url);
  url.searchParams.set("copy", String(copy));
  return (await import(url.href)) as { sharedState: typeof sharedState };
}

await test("two module copies see one sharedState", async () => {
  const first = await stateCopy(1);
  const second = await stateCopy(2);
  assert.notEqual(first.sharedState, second.sharedState);
  assert.equal(first.sharedState(), second.sharedState());
  assert.equal(sharedState(), first.sharedState());
  assert.ok(sharedState().queue instanceof ShellQueue);
  assert.ok(sharedState().messaged instanceof Set);
  assert.ok(sharedState().stopped instanceof Set);
});

await test("take returns the role recorded for the command and cwd", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1001), "pm");
});

await test("an entry is taken once", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  queue.take("ls", "/work", 1001);
  assert.equal(queue.take("ls", "/work", 1002), undefined);
});

await test("no entry sets no role", () => {
  const queue = new ShellQueue();
  assert.equal(queue.take("ls", "/work", 1000), undefined);
});

await test("an entry for another command or cwd is left alone", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm", "ls", "/work"), 1000);
  assert.equal(queue.take("pwd", "/work", 1001), undefined);
  assert.equal(queue.take("ls", "/other", 1001), undefined);
  assert.equal(queue.take("ls", "/work", 1001), "pm");
});

await test("an entry whose role is undefined takes no role", () => {
  const queue = new ShellQueue();
  queue.record(entry(undefined), 1000);
  assert.equal(queue.take("ls", "/work", 1001), undefined);
});

await test("test_same_key_different_roles_gets_pm", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work", "ses_a"), 1000);
  queue.record(entry("pm", "ls", "/work", "ses_b"), 1001);
  assert.equal(queue.take("ls", "/work", 1002), "pm");
});

await test("a role and no role under one key also get pm", () => {
  const queue = new ShellQueue();
  queue.record(entry(undefined, "ls", "/work", "ses_a"), 1000);
  queue.record(entry("gm", "ls", "/work", "ses_b"), 1001);
  assert.equal(queue.take("ls", "/work", 1002), "pm");
});

await test("oldest entry first", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work", "ses_a"), 1000);
  queue.record(entry("gm", "ls", "/work", "ses_b"), 1001);
  queue.record(entry("pm", "ls", "/other", "ses_c"), 1002);
  assert.equal(queue.take("ls", "/work", 1003), "gm");
  assert.equal(queue.take("ls", "/work", 1004), "gm");
  assert.equal(queue.take("ls", "/work", 1005), undefined);
  assert.equal(queue.take("ls", "/other", 1006), "pm");
});

await test("expired entry ignored", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1000 + 30_000), undefined);
});

await test("an entry just inside the expiry still pairs", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1000 + 29_999), "pm");
});

await test("an expired entry does not turn a live one into pm", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work", "ses_a"), 1000);
  queue.record(entry("pm", "ls", "/work", "ses_b"), 40_000);
  assert.equal(queue.take("ls", "/work", 40_001), "pm");
  queue.record(entry("gm", "ls", "/work", "ses_a"), 50_000);
  assert.equal(queue.take("ls", "/work", 50_001), "gm");
});

await test("a relative workdir pairs by its raw string", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm", "make", "sub"), 1000);
  assert.equal(queue.take("make", "/work/sub", 1001), undefined);
  assert.equal(queue.take("make", "sub", 1001), "pm");
});

await test("scrub removes password and key, keeps PATH and HOME", () => {
  const env: Record<string, string | undefined> = {
    [PASSWORD]: "p",
    OPENCODE_PASSWORD: "p",
    [KEY]: "k",
    OPENCODE_API_TOKEN: "t",
    OPENCODE_SIGNING_KEY: "s",
    OPENCODE_ADMIN_PASSWORD: "a",
    OPENCODE_TOKEN: "t",
    OPENCODE_KEY: "k",
    OPENCODE_X_TOKEN: "x",
    PATH: "/bin",
    HOME: "/h",
  };
  scrubEnv(env, KEY);
  assert.deepEqual(env, { PATH: "/bin", HOME: "/h" });
});

await test("scrub leaves near misses alone", () => {
  const env: Record<string, string | undefined> = {
    OPENCODE_SERVER_USERNAME: "opencode",
    OPENCODE_PORT: "1",
    OTHER_TOKEN: "t",
    OTHER_KEY: "k",
    OTHER_PASSWORD: "p",
    OPENCODE_TOKEN_FILE: "f",
    opencode_api_token: "lower",
    [`${KEY}_2`]: "x",
  };
  const before = { ...env };
  scrubEnv(env, KEY);
  assert.deepEqual(env, before);
});

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
    get: (id) => {
      const found = sessions[id];
      return found instanceof Error
        ? Promise.reject(found)
        : Promise.resolve(found);
    },
  });
  return { clock, queue, ...hooks };
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
