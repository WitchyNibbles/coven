import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { pmLaunchTool } from "../src/pm/launch.ts";
import { sharedState } from "../src/pm/state.ts";
import { ToolError } from "../src/pm/tools.ts";
import { coven } from "./pm-fake-session.ts";
import {
  GM,
  SECRET,
  call,
  gitRepo,
  gmWorld,
  quietStatus,
} from "./pm-gm-world.ts";
import { deps, list, resume, stop } from "./pm-manage-kit.ts";

const MINUTE = 60_000;

const PM1: SessionLike = {
  id: "pm1",
  metadata: coven("pm", "acme-1"),
  outcome: "succeeded",
};

await test("list shows code, id, busy/idle, outcome, ASK WAITING (bug) with request id, missing, child busy 30m", async () => {
  const world = gmWorld([
    GM,
    PM1,
    { id: "pm2", metadata: coven("pm", "beta"), time: { busy: 5 } },
    { id: "pm3", metadata: coven("pm", "gamma") },
    { id: "kid", parentID: "pm1", metadata: coven("pm", "acme-1") },
    { id: "old", metadata: coven("former-gm") },
  ]);
  const get = world.get.bind(world);
  world.get = (input) =>
    input.sessionID === "pm3"
      ? Promise.reject(new Error("Session not found"))
      : get(input);
  const out = await list(
    world,
    quietStatus({
      watch: (id) =>
        Promise.resolve({
          asks: id === "pm2" ? ["req-9", "req-10"] : [],
          childBusyMs: id === "pm1" ? 30 * MINUTE : 5 * MINUTE,
        }),
      lastMessage: (id) =>
        Promise.resolve(
          id === "pm1" ? "DONE acme-1/1 merged\nmore" : undefined,
        ),
    }),
  );
  assert.deepEqual(out.split("\n"), [
    'pm-acme-1  pm1  idle  last succeeded  child busy 30m  "DONE acme-1/1 merged"',
    "pm-beta  pm2  busy  ASK WAITING (bug) req-9  ASK WAITING (bug) req-10",
    "pm-gamma  pm3  missing",
  ]);
});

await test("list cuts a long last message and says so when there is no PM", async () => {
  const world = gmWorld([GM, PM1]);
  const out = await list(
    world,
    quietStatus({ lastMessage: () => Promise.resolve("x".repeat(300)) }),
  );
  assert.ok(out.endsWith(`"${"x".repeat(199)}…"`), out);
  assert.equal(await list(gmWorld([GM])), "no PM sessions");
});

await test("list is a tool error when the listing or a status read fails", async () => {
  const down = gmWorld([GM, PM1]);
  down.failList = true;
  await assert.rejects(list(down), ToolError);
  const world = gmWorld([GM, PM1]);
  const failing = quietStatus({
    watch: () => Promise.reject(new Error("no asks")),
  });
  await assert.rejects(
    list(world, failing),
    (error: unknown) =>
      error instanceof ToolError && /no asks/.test(error.message),
  );
});

await test('resume prompts "RESUMED by gm\\n<text>" on a live PM and writes nothing', async () => {
  const world = gmWorld([GM, PM1]);
  await resume(world, { code: "acme-1", text: "carry on" });
  assert.deepEqual(world.prompts, [
    { sessionID: "pm1", text: "RESUMED by gm\ncarry on", delivery: "queue" },
  ]);
  assert.deepEqual(world.updates, []);
});

await test("stop interrupts, marks the stop in sharedState and keeps the session", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  const out = await stop(world, { code: "acme-1" });
  assert.deepEqual(world.interrupts, ["pm1"]);
  assert.equal(sharedState().stopped.has("pm1"), true);
  assert.deepEqual(world.updates, []);
  assert.equal(out, "stopped pm-acme-1");
});

await test("stop is a tool error for an unknown code or a failed interrupt, which leaves no stop mark", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  await assert.rejects(stop(world, { code: "nope" }), /no PM with code "nope"/);
  await assert.rejects(
    stop(world, { code: "acme-1", archive: "yes" }),
    ToolError,
  );
  world.failInterrupt = true;
  await assert.rejects(
    stop(world, { code: "acme-1" }),
    (error: unknown) =>
      error instanceof ToolError && /interrupt down/.test(error.message),
  );
  assert.equal(sharedState().stopped.has("pm1"), false);
});

await test("each of launch, list, resume and stop is refused from a non-GM session", async () => {
  const world = gmWorld([
    GM,
    PM1,
    { id: "kid", parentID: "pm1" },
    { id: "gmkid", parentID: "gm1" },
    { id: "plain" },
    { id: "old", metadata: coven("former-gm") },
  ]);
  const launcher = pmLaunchTool({
    ...deps(world),
    options: {
      store: null,
      profileDir: "/placeholder/profile",
      secretRules: SECRET,
    },
  });
  const repo = gitRepo();
  for (const caller of ["pm1", "kid", "gmkid", "plain", "old", "unknown"]) {
    for (const attempt of [
      () => call(launcher, caller, { code: "z", repo, brief: "go" }),
      () => list(world, quietStatus(), caller),
      () => resume(world, { code: "acme-1", text: "x" }, caller),
      () => stop(world, { code: "acme-1" }, caller),
    ]) {
      await assert.rejects(attempt, Error, caller);
    }
  }
  assert.equal(world.created.length, 0);
  assert.equal(world.prompts.length, 0);
  assert.deepEqual(world.interrupts, []);
});

await test("a GM tool is refused with the GM-only reason", async () => {
  const world = gmWorld([GM, PM1]);
  await assert.rejects(
    stop(world, { code: "acme-1" }, "pm1"),
    /only the GM can/,
  );
});

await test("list reads the live sessions once, for the PM roots and the busy set together", async () => {
  const world = gmWorld([
    GM,
    PM1,
    { id: "pm2", metadata: coven("pm", "beta"), time: { busy: 5 } },
  ]);
  const out = await list(world);
  // one walk of the live listing: the page with the sessions and the empty page 2.3.2 ends with
  assert.equal(world.calls.filter((each) => each === "list").length, 2);
  assert.deepEqual(out.split("\n"), [
    "pm-acme-1  pm1  idle  last succeeded",
    "pm-beta  pm2  busy",
  ]);
});

await test(
  "list describes the PMs together and keeps them in listing order",
  { timeout: 2000 },
  async () => {
    const world = gmWorld([
      GM,
      { id: "pm1", metadata: coven("pm", "one") },
      { id: "pm2", metadata: coven("pm", "two") },
      { id: "pm3", metadata: coven("pm", "three") },
    ]);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((done) => {
      release = done;
    });
    const out = await list(
      world,
      quietStatus({
        lastMessage: async (id) => {
          // the first PM waits for the last one to be asked: sequential reads would never finish
          if (id === "pm3") {
            release();
          } else if (id === "pm1") {
            await gate;
          }
          return `said by ${id}`;
        },
      }),
    );
    assert.deepEqual(out.split("\n"), [
      'pm-one  pm1  idle  "said by pm1"',
      'pm-two  pm2  idle  "said by pm2"',
      'pm-three  pm3  idle  "said by pm3"',
    ]);
  },
);

await test("list is a tool error when the live listing never ends", async () => {
  const world = gmWorld([GM, PM1]);
  world.endless = true;
  await assert.rejects(
    list(world),
    (error: unknown) =>
      error instanceof ToolError && /more than \d+ pages/.test(error.message),
  );
});
