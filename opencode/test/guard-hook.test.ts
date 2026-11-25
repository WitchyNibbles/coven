import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import type { ExecuteBeforeEvent } from "../src/context.ts";
import { exits, never } from "./guard-fake.ts";
import type { Behaviour } from "./guard-fake.ts";
import {
  DIRECTORY,
  LEAD,
  PROFILE_OPTIONS,
  bootCoven,
} from "./plugin-fixture.ts";

const COVEN = {
  covenRoot: "/c",
  keyEnv: "K",
  ...PROFILE_OPTIONS,
  toolChoiceAuto: ["litellm"],
};

function executeBefore(tool: string, input: unknown): ExecuteBeforeEvent {
  return {
    tool,
    sessionID: LEAD,
    agent: "build",
    messageID: "msg_1",
    id: "call_1",
    input,
  };
}

const toStop: (() => void)[] = [];

async function guardHook(setup: Parameters<typeof bootCoven>[0] = {}) {
  const p = bootCoven({ options: COVEN, ...setup });
  const cleanup = await p.outcome;
  toStop.push(cleanup);
  const hooks = p.toolHooks.filter((hook) => hook.name === "execute.before");
  assert.equal(hooks.length, 1);
  const hook = hooks[0];
  assert.ok(hook !== undefined);
  return { p, cleanup, hook };
}

/** Refuses a payload that names the env file, allows the rest. */
const refuseEnv: Behaviour = (child) => {
  if (child.call.stdin.includes('"file_path":".env"')) {
    child.finish(2, "coven deny-secrets: access to '.env' is blocked");
  } else {
    child.finish(0);
  }
};

function patchOf(...lines: string[]): { patchText: string } {
  return {
    patchText: ["*** Begin Patch", ...lines, "*** End Patch"].join("\n"),
  };
}

function files(p: { spawned: readonly { stdin: string }[] }): string[] {
  return p.spawned.map(
    (call) =>
      (JSON.parse(call.stdin) as { tool_input: { file_path: string } })
        .tool_input.file_path,
  );
}

function settle(promise: Promise<void> | void): Promise<string> {
  return Promise.resolve(promise).then(
    () => "allowed",
    (error: unknown) => (error instanceof Error ? error.message : "other"),
  );
}

await test("setup registers one execute.before hook and cleanup disposes it", async () => {
  const { hook, cleanup, p } = await guardHook();
  assert.equal(hook.disposed, 0);
  cleanup();
  assert.equal(hook.disposed, 1);
  assert.equal(p.subscriptions[0]?.signal.aborted, true);
});

await test("a failed setup disposes the execute.before hook", async () => {
  const p = bootCoven({ options: COVEN, unlisted: ["coven_ping"] });
  await assert.rejects(p.outcome, /tools not registered/);
  assert.equal(p.toolHooks.length, 1);
  assert.equal(p.toolHooks[0]?.disposed, 1);
});

await test("a refused call throws the guard's text from the hook", async () => {
  const { hook } = await guardHook({ guard: refuseEnv });
  assert.equal(
    await settle(hook.callback(executeBefore("read", { path: ".env" }))),
    "coven deny-secrets: access to '.env' is blocked",
  );
});

await test("an allowed call resolves and the payload carries the session directory", async () => {
  const { p, hook } = await guardHook();
  await hook.callback(executeBefore("shell", { command: "ls", workdir: "/x" }));
  assert.deepEqual(
    p.spawned.map((call) => JSON.parse(call.stdin) as unknown),
    [
      {
        tool_name: "Bash",
        tool_input: { command: "ls", workdir: "/x" },
        cwd: DIRECTORY,
        hook_event_name: "PreToolUse",
      },
    ],
  );
  assert.equal(p.spawned[0]?.command, "sh");
  assert.deepEqual(p.spawned[0]?.args, [
    "/c/hooks/fail-closed.sh",
    "/c/hooks/deny_secrets.py",
  ]);
});

await test("a tool the guard does not check starts no child", async () => {
  const { p, hook } = await guardHook();
  await hook.callback(executeBefore("webfetch", { url: "https://x" }));
  assert.deepEqual(p.spawned, []);
});

await test("a checked tool with a non-object input is refused", async () => {
  const { p, hook } = await guardHook();
  assert.match(
    await settle(hook.callback(executeBefore("read", "oops"))),
    /unreadable read input; blocking/,
  );
  assert.deepEqual(p.spawned, []);
});

await test("a patch is checked file by file and every payload is awaited", async () => {
  const { p, hook } = await guardHook();
  await hook.callback(
    executeBefore(
      "patch",
      patchOf(
        "*** Add File: a.txt",
        "+x",
        "*** Update File: b.txt",
        "*** Move to: c.txt",
      ),
    ),
  );
  assert.deepEqual(files(p), ["a.txt", "b.txt", "c.txt"]);
});

await test("a patch throws on the first refusal and checks no further file", async () => {
  const { p, hook } = await guardHook({ guard: refuseEnv });
  const input = patchOf(
    "*** Add File: a.txt",
    "*** Update File: b.txt",
    "*** Move to: .env",
    "*** Delete File: d.txt",
  );
  assert.match(
    await settle(hook.callback(executeBefore("patch", input))),
    /access to '\.env' is blocked/,
  );
  assert.deepEqual(files(p), ["a.txt", "b.txt", ".env"]);
});

await test("a patch with no file is refused before any child starts", async () => {
  const { p, hook } = await guardHook();
  assert.match(
    await settle(hook.callback(executeBefore("patch", patchOf()))),
    /no file in patch; blocking/,
  );
  assert.deepEqual(p.spawned, []);
});

await test("a guard that fails blocks the call", async () => {
  const { hook } = await guardHook({ guard: exits(1) });
  assert.match(
    await settle(hook.callback(executeBefore("grep", { pattern: "p" }))),
    /guard failed \(exit 1\); blocking/,
  );
});

async function timedOut(
  options: Record<string, unknown>,
  ms: number,
): Promise<string> {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { hook } = await guardHook({ options, guard: never });
    const outcome = settle(hook.callback(executeBefore("read", { path: "a" })));
    await tick();
    mock.timers.tick(ms - 1);
    await tick();
    mock.timers.tick(1);
    return await outcome;
  } finally {
    mock.timers.reset();
  }
}

await test("the hook uses guardTimeoutMs as the timeout", async () => {
  assert.equal(
    await timedOut({ ...COVEN, guardTimeoutMs: 7000 }, 7000),
    "coven deny-secrets: guard timed out after 7000 ms; blocking",
  );
});

await test("the default timeout is five seconds", async () => {
  assert.match(await timedOut(COVEN, 5000), /timed out after 5000 ms/);
});

await test("the joins the guard tests started stop", () => {
  toStop.forEach((stop) => {
    stop();
  });
});
