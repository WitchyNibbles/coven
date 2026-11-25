import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { pmLaunchTool } from "../src/pm/launch.ts";
import { pmListTool, pmResumeTool, pmStopTool } from "../src/pm/manage.ts";
import type { PmStatus } from "../src/pm/manage.ts";
import { resolveRole } from "../src/pm/role.ts";
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
import type { World } from "./pm-gm-world.ts";

const MINUTE = 60_000;

const PM1: SessionLike = {
  id: "pm1",
  metadata: coven("pm", "acme-1"),
  outcome: "succeeded",
};

function deps(world: World) {
  return { session: world, resolve: resolveRole };
}

function list(world: World, status: PmStatus = quietStatus(), as = "gm1") {
  return call(pmListTool({ ...deps(world), status }), as, {});
}

function resume(world: World, input: unknown, as = "gm1") {
  return call(pmResumeTool(deps(world)), as, input);
}

function stop(world: World, input: unknown, as = "gm1") {
  return call(pmStopTool(deps(world)), as, input);
}

await test("list shows code, id, busy/idle, outcome, ASK WAITING (bug) with request id, missing, child busy 30m", async () => {
  const world = gmWorld([
    GM,
    PM1,
    { id: "pm2", metadata: coven("pm", "beta") },
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
      active: () => Promise.resolve(new Set(["pm2"])),
      asks: (id) => Promise.resolve(id === "pm2" ? ["req-9", "req-10"] : []),
      childBusyMs: (id) =>
        Promise.resolve(id === "pm1" ? 30 * MINUTE : 5 * MINUTE),
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
    asks: () => Promise.reject(new Error("no asks")),
  });
  await assert.rejects(
    list(world, failing),
    (error: unknown) =>
      error instanceof ToolError && /no asks/.test(error.message),
  );
});

await test('resume prompts "RESUMED by gm\\n<text>" on a live PM without touching the archive', async () => {
  const world = gmWorld([GM, PM1]);
  await resume(world, { code: "acme-1", text: "carry on" });
  assert.deepEqual(world.prompts, [
    { sessionID: "pm1", text: "RESUMED by gm\ncarry on", delivery: "queue" },
  ]);
  assert.equal(
    world.calls.some((each) => each.startsWith("archive")),
    false,
  );
});

await test("resume unarchives then prompts, and forgets an earlier stop", async () => {
  const world = gmWorld([GM, PM1]);
  world.archived.add("pm1");
  sharedState().stopped.add("pm1");
  await resume(world, { code: "acme-1", text: "back" });
  assert.deepEqual(
    world.calls.filter((each) => /^(archive|prompt)/.test(each)),
    ["archive:pm1:false", "prompt:pm1"],
  );
  assert.equal(sharedState().stopped.has("pm1"), false);
});

await test("resume is a tool error for an unknown code, empty text or a failed unarchive", async () => {
  const world = gmWorld([GM, PM1]);
  await assert.rejects(
    resume(world, { code: "nope", text: "x" }),
    /no PM with code "nope"/,
  );
  await assert.rejects(resume(world, { code: "acme-1", text: " " }), ToolError);
  await assert.rejects(resume(world, { text: "x" }), ToolError);
  assert.equal(world.prompts.length, 0);
  world.archived.add("pm1");
  world.failUpdate = 1;
  await assert.rejects(
    resume(world, { code: "acme-1", text: "x" }),
    (error: unknown) =>
      error instanceof ToolError && /update down/.test(error.message),
  );
  assert.equal(world.prompts.length, 0);
});

await test("stop interrupts, marks the stop in sharedState and keeps the session", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  await stop(world, { code: "acme-1" });
  assert.deepEqual(world.interrupts, ["pm1"]);
  assert.equal(sharedState().stopped.has("pm1"), true);
  assert.equal(
    world.calls.some((each) => each.startsWith("archive")),
    false,
  );
});

await test("stop with archive true archives after the interrupt", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  await stop(world, { code: "acme-1", archive: true });
  assert.deepEqual(
    world.calls.filter((each) => /^(interrupt|archive)/.test(each)),
    ["interrupt:pm1", "archive:pm1:true"],
  );
  assert.ok(world.archived.has("pm1"));
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

await test("list reads the busy set once", async () => {
  const world = gmWorld([
    GM,
    PM1,
    { id: "pm2", metadata: coven("pm", "beta") },
  ]);
  let reads = 0;
  await list(
    world,
    quietStatus({
      active: () => {
        reads += 1;
        return Promise.resolve(new Set<string>());
      },
    }),
  );
  assert.equal(reads, 1);
});
