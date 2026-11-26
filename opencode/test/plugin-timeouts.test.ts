import assert from "node:assert/strict";
import { mock, test } from "node:test";

import { exits, never } from "./guard-fake.ts";
import type { Behaviour } from "./guard-fake.ts";
import {
  COVEN,
  SCRIPTS_FOR_PM,
  SERVER,
  booted,
  cleanups,
  perScript,
  scripts,
  settleStages,
  toolEvent,
} from "./plugin-guards-fixture.ts";
import { PM, bootCoven } from "./plugin-fixture.ts";

/** The outcome of a call whose `script` never answers, `ms` after it starts. */
async function stalled(
  script: string,
  ms: number,
  late: Behaviour = never,
): Promise<{ before: string; after: string; spawned: string[] }> {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { p, run } = await booted({ guard: perScript({ [script]: late }) });
    const outcome = run(toolEvent(PM)).then(
      () => "allowed",
      (error: unknown) => (error instanceof Error ? error.message : "other"),
    );
    let state = "pending";
    void outcome.then((text) => {
      state = text;
    });
    await settleStages();
    mock.timers.tick(ms - 1);
    await settleStages();
    const before = state;
    mock.timers.tick(1);
    await settleStages();
    return { before, after: state, spawned: scripts(p) };
  } finally {
    mock.timers.reset();
  }
}

await test("each hook gets its own timeout", async () => {
  const server = await stalled(SERVER, 5000);
  assert.equal(server.before, "pending");
  assert.equal(
    server.after,
    "coven opencode-server-guard: guard timed out after 5000 ms; blocking",
  );
  assert.deepEqual(server.spawned, SCRIPTS_FOR_PM.slice(0, 2));

  const pm = await stalled("pm_permissions.py", 15_000);
  assert.equal(pm.before, "pending");
  assert.equal(
    pm.after,
    "coven pm-permissions: guard timed out after 15000 ms; blocking",
  );
});

await test("a fake that answers at 10 s passes pm-permissions and times out the server guard", async () => {
  const answersAtTen: Behaviour = (child) => {
    setTimeout(() => {
      child.finish(0);
    }, 10_000);
  };
  const server = await stalled(SERVER, 5000, answersAtTen);
  assert.match(
    server.after,
    /opencode-server-guard: guard timed out after 5000 ms/,
  );

  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { p, run } = await booted({
      guard: perScript({ "pm_permissions.py": answersAtTen }),
    });
    const outcome = run(toolEvent(PM));
    await settleStages();
    mock.timers.tick(10_000);
    await outcome;
    assert.deepEqual(scripts(p), SCRIPTS_FOR_PM);
  } finally {
    mock.timers.reset();
  }
});

await test("a prod guard timeout refuses", async () => {
  const prod = await stalled("prod_guard.py", 30_000);
  assert.equal(prod.before, "pending");
  assert.equal(
    prod.after,
    "coven prod-guard: guard timed out after 30000 ms; blocking",
  );
  assert.deepEqual(prod.spawned, SCRIPTS_FOR_PM);
});

await test("deny-secrets keeps the guardTimeoutMs option", async () => {
  const slow = await stalled("deny_secrets.py", 5000);
  assert.equal(
    slow.after,
    "coven deny-secrets: guard timed out after 5000 ms; blocking",
  );
});

await test("selfCheck fails when python3 is too old", async () => {
  const p = bootCoven({ options: COVEN, python: exits(1) });
  await assert.rejects(p.outcome, /python3 3\.12 or newer/);
  assert.equal(p.subscriptions[0]?.signal.aborted, true);
  const [call] = p.pythonChecks;
  assert.ok(call !== undefined);
  assert.equal(call.command, "python3");
  assert.ok(call.args.some((arg) => arg.includes("(3, 12)")));
});

await test("selfCheck fails when python3 is missing or times out", async () => {
  const missing = bootCoven({
    options: COVEN,
    python: (child) => {
      child.emit("error", new Error("spawn python3 ENOENT"));
    },
  });
  await assert.rejects(missing.outcome, /python3 3\.12 or newer/);
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const hung = bootCoven({ options: COVEN, python: never });
    const refused = assert.rejects(hung.outcome, /python3 3\.12 or newer/);
    await settleStages();
    mock.timers.tick(5000);
    await refused;
  } finally {
    mock.timers.reset();
  }
});

await test("a python3 that passes lets setup finish", async () => {
  const p = bootCoven({ options: COVEN, python: exits(0) });
  cleanups.push(await p.outcome);
  assert.equal(p.pythonChecks.length, 1);
  assert.deepEqual(p.spawned, []);
});

await test("the plugins the guard tests started stop", () => {
  cleanups.forEach((stop) => {
    stop();
  });
});
