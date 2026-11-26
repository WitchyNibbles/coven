import assert from "node:assert/strict";
import { test } from "node:test";

import type { EvaluateEvent } from "../src/context.ts";
import { ROLE_ENV } from "../src/pm/role.ts";
import { sharedState } from "../src/pm/state.ts";
import {
  DIRECTORY,
  LEAD,
  PM,
  PM_KID,
  PM_TOOLS,
  PROFILE_OPTIONS,
  bootCoven,
  ended,
  started,
} from "./plugin-fixture.ts";
import { toolEvent } from "./plugin-guards-fixture.ts";

const COVEN = {
  covenRoot: "/c",
  keyEnv: "K",
  ...PROFILE_OPTIONS,
  toolChoiceAuto: [],
};

const cleanups: (() => void)[] = [];

async function booted(setup: Parameters<typeof bootCoven>[0] = {}) {
  const p = bootCoven({ options: COVEN, ...setup });
  cleanups.push(await p.outcome);
  return p;
}

function evaluateEvent(
  sessionID: string,
  effect: EvaluateEvent["effect"],
): EvaluateEvent {
  return { sessionID, action: "shell", resources: ["ls"], effect };
}

async function evaluate(
  p: Awaited<ReturnType<typeof booted>>,
  event: EvaluateEvent,
): Promise<EvaluateEvent> {
  assert.equal(p.evaluateHooks.length, 1);
  await p.evaluateHooks[0]?.(event);
  return event;
}

await test("pmSetup registers an evaluate hook that turns a PM ask into a deny", async () => {
  const p = await booted();
  const event = await evaluate(p, evaluateEvent(PM, "ask"));
  assert.equal(event.effect, "deny");
  assert.match(event.message ?? "", /a PM may not wait on a prompt/);
});

await test("the evaluate hook turns an ask of a PM's child into a deny too", async () => {
  const p = await booted();
  assert.equal(
    (await evaluate(p, evaluateEvent(PM_KID, "ask"))).effect,
    "deny",
  );
});

await test("the evaluate hook leaves the GM's ask and every other effect alone", async () => {
  const p = await booted();
  assert.equal((await evaluate(p, evaluateEvent(LEAD, "ask"))).effect, "ask");
  assert.equal((await evaluate(p, evaluateEvent(PM, "allow"))).effect, "allow");
  assert.equal(
    (await evaluate(p, evaluateEvent(PM, "deny"))).message,
    undefined,
  );
});

await test("a PM's shell gets the role variable and the GM's shell does not", async () => {
  const p = await booted();
  const hook = p.toolHooks.find((entry) => entry.name === "execute.before");
  const shell = p.shellHooks[0];
  assert.ok(hook !== undefined && shell !== undefined);
  for (const [sessionID, expected] of [
    [PM, "pm"],
    [LEAD, undefined],
  ] as const) {
    await hook.callback(toolEvent(sessionID, { command: "ls" }));
    const event = {
      command: "ls",
      cwd: DIRECTORY,
      env: { K: "secret", PATH: "/bin" } as Record<string, string>,
    };
    await shell.callback(event);
    assert.equal(event.env[ROLE_ENV], expected);
    assert.equal(event.env["K"], undefined);
  }
});

await test("a PM turn that ends without a message queues a notice into the GM", async () => {
  const p = await booted();
  p.messages.set(PM, [
    { type: "assistant", content: [{ type: "text", text: "all done" }] },
  ]);
  await p.emit(ended(PM), 1);
  assert.deepEqual(p.prompts, [
    {
      sessionID: LEAD,
      text: "[coven notice] pm-proj ended a turn (succeeded) without a message. Last line: all done",
      delivery: "queue",
    },
  ]);
});

await test("a stop mark from an idle PM is cleared when its next turn starts", async () => {
  const p = await booted();
  sharedState().stopped.add(PM);
  sharedState().messaged.add(PM);
  await p.emit(started(PM), 1);
  assert.equal(sharedState().stopped.has(PM), false);
  assert.equal(sharedState().messaged.has(PM), false);
  await p.emit(ended(PM), 1);
  assert.equal(p.prompts.length, 1);
});

await test("a notice that cannot be delivered goes to the serve log", async () => {
  const p = await booted();
  p.sessions.delete(LEAD);
  await p.emit(ended(PM, "failed"), 1);
  assert.deepEqual(p.prompts, []);
  assert.equal(p.serveLogs.length, 1);
  assert.match(p.serveLogs[0] ?? "", /\(failed\) without a message/);
  assert.match(p.serveLogs[0] ?? "", /no GM session/);
});

await test("coven_pm_list reads the PM's state from the server's own records", async () => {
  const p = await booted();
  const list = p.tools.find((tool) => tool.name === "coven_pm_list");
  assert.ok(list !== undefined);
  p.asks.set(PM, [{ id: "per_7" }]);
  p.messages.set(PM, [
    { type: "assistant", content: [{ type: "text", text: "working on it" }] },
  ]);
  const out = await list.execute(
    {},
    {
      sessionID: LEAD,
      signal: new AbortController().signal,
      progress: () => Promise.resolve(),
    },
  );
  assert.equal(
    out.content,
    'pm-proj  ses_pm  idle  ASK WAITING (bug) per_7  "working on it"',
  );
});

await test("cleanup stops the notice subscription and disposes the one execute.before hook", async () => {
  const p = bootCoven({ options: COVEN });
  const cleanup = await p.outcome;
  assert.equal(p.subscriptions.length, 2);
  cleanup();
  assert.equal(p.subscriptions[1]?.signal.aborted, true);
  assert.equal(p.toolHooks.length, 1);
  assert.equal(p.toolHooks[0]?.disposed, 1);
});

await test("the self-check fails when any hierarchy tool is missing", async () => {
  for (const name of PM_TOOLS) {
    const p = bootCoven({ options: COVEN, unlisted: [name] });
    await assert.rejects(
      p.outcome,
      new RegExp(`^Error: coven: tools not registered: ${name}$`),
    );
  }
});

await test("teardown disposes the shell and evaluate registrations", async () => {
  const p = bootCoven({ options: COVEN });
  const cleanup = await p.outcome;
  cleanup();
  assert.deepEqual([...p.disposals].sort(), ["create.before", "evaluate"]);
});

await test("teardown copes with registrations that cannot be disposed", async () => {
  const p = bootCoven({ options: COVEN, disposable: false });
  const cleanup = await p.outcome;
  cleanup();
  assert.deepEqual(p.disposals, []);
});

await test("a child busy since before the plugin started does not hold a PM notice back", async () => {
  const p = await booted();
  p.sessions.set(PM_KID, { id: PM_KID, parentID: PM, time: { busy: 999_000 } });
  await p.emit(ended(PM), 1);
  assert.equal(p.prompts.length, 1);
});

await test("a child busy since after the plugin started holds it back, and the log says so", async () => {
  const p = await booted();
  p.sessions.set(PM_KID, {
    id: PM_KID,
    parentID: PM,
    time: { busy: 1_000_001 },
  });
  await p.emit(ended(PM), 1);
  assert.deepEqual(p.prompts, []);
  assert.match(p.serveLogs[0] ?? "", /held back for pm-proj: child ses_pm_kid/);
});

await test("a setup that fails on the evaluate hook still disposes the shell hook it registered", async () => {
  const p = bootCoven({ options: COVEN, failEvaluate: true });
  await assert.rejects(p.outcome, /evaluate refused/);
  assert.deepEqual(p.disposals, ["create.before"]);
});

await test("a setup that fails rejects with its own error even when the teardown throws", async () => {
  const p = bootCoven({
    options: COVEN,
    failEvaluate: true,
    guardDisposeThrows: true,
  });
  await assert.rejects(p.outcome, /evaluate refused/);
  assert.deepEqual(p.disposals, ["create.before"]);
  assert.ok(
    p.logs.some((line) => /cleanup failed.*guard dispose failed/.test(line)),
  );
});

await test("teardown disposes the registrations even when the execute.before hook cannot be disposed", async () => {
  const p = bootCoven({ options: COVEN, guardDisposeThrows: true });
  const cleanup = await p.outcome;
  assert.throws(cleanup, /guard dispose failed/);
  assert.deepEqual([...p.disposals].sort(), ["create.before", "evaluate"]);
  assert.equal(p.subscriptions[1]?.signal.aborted, true);
});

await test("the plugins this file started stop", () => {
  for (const stop of cleanups) {
    stop();
  }
});
