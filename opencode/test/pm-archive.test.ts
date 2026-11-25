import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { pmListTool } from "../src/pm/manage.ts";
import { sharedState } from "../src/pm/state.ts";
import { ToolError } from "../src/pm/tools.ts";
import { deps, list, resume, stop } from "./pm-manage-kit.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, call, gmWorld, quietStatus } from "./pm-gm-world.ts";

const PM1: SessionLike = {
  id: "pm1",
  metadata: coven("pm", "acme-1"),
  outcome: "succeeded",
};

const HIDDEN: SessionLike = {
  id: "pm1",
  metadata: {
    other: { keep: 1 },
    coven: { role: "pm", code: "acme-1", note: "kept", archived: true },
  },
};

await test("resume clears the hidden flag, keeps every other metadata key, then prompts, and forgets an earlier stop", async () => {
  const world = gmWorld([GM, HIDDEN]);
  sharedState().stopped.add("pm1");
  await resume(world, { code: "acme-1", text: "back" });
  assert.deepEqual(world.updates, [
    {
      sessionID: "pm1",
      metadata: {
        other: { keep: 1 },
        coven: { role: "pm", code: "acme-1", note: "kept" },
      },
    },
  ]);
  assert.deepEqual(
    world.calls.filter((each) => /^(update|prompt)/.test(each)),
    ["update:pm1:pm", "prompt:pm1"],
  );
  assert.equal(sharedState().stopped.has("pm1"), false);
});

await test("resume is a tool error for an unknown code, empty text or a failed flag update", async () => {
  const world = gmWorld([GM, HIDDEN]);
  await assert.rejects(
    resume(world, { code: "nope", text: "x" }),
    /no PM with code "nope"/,
  );
  await assert.rejects(resume(world, { code: "acme-1", text: " " }), ToolError);
  await assert.rejects(resume(world, { text: "x" }), ToolError);
  assert.equal(world.prompts.length, 0);
  world.failUpdate = 1;
  await assert.rejects(
    resume(world, { code: "acme-1", text: "x" }),
    (error: unknown) =>
      error instanceof ToolError && /update down/.test(error.message),
  );
  assert.equal(world.prompts.length, 0);
});

await test("resume of a PM archived in OpenCode is a tool error that says to unarchive it there, and sends and writes nothing", async () => {
  const world = gmWorld([GM, PM1]);
  world.archived.add("pm1");
  sharedState().stopped.add("pm1");
  await assert.rejects(
    resume(world, { code: "acme-1", text: "back" }),
    (error: unknown) =>
      error instanceof ToolError &&
      /pm-acme-1 is archived in OpenCode.*unarchive it there first/.test(
        error.message,
      ),
  );
  assert.equal(world.prompts.length, 0);
  assert.deepEqual(world.updates, []);
  assert.equal(sharedState().stopped.has("pm1"), true);
});

await test("stop with archive true interrupts, then sets coven.archived and keeps every other metadata key", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([
    GM,
    {
      id: "pm1",
      metadata: {
        other: { keep: 1 },
        coven: { role: "pm", code: "acme-1", note: "kept" },
      },
    },
  ]);
  const out = await stop(world, { code: "acme-1", archive: true });
  assert.deepEqual(
    world.calls.filter((each) => /^(interrupt|update)/.test(each)),
    ["interrupt:pm1", "update:pm1:pm"],
  );
  assert.deepEqual(world.updates, [
    {
      sessionID: "pm1",
      metadata: {
        other: { keep: 1 },
        coven: { role: "pm", code: "acme-1", note: "kept", archived: true },
      },
    },
  ]);
  assert.equal(
    out,
    "stopped pm-acme-1; hidden from coven_pm_list; the session is not archived in OpenCode",
  );
});

await test("stop with archive true reads the metadata fresh, not from the listing", async () => {
  const world = gmWorld([GM, PM1]);
  // the PM's metadata changed after the listing was read
  const get = world.get.bind(world);
  world.get = async (input) =>
    input.sessionID === "pm1"
      ? {
          ...(await get(input)),
          metadata: { fresh: true, ...coven("pm", "acme-1") },
        }
      : get(input);
  await stop(world, { code: "acme-1", archive: true });
  assert.deepEqual(world.updates[0]?.metadata, {
    fresh: true,
    coven: { role: "pm", code: "acme-1", archived: true },
  });
});

await test("stop with archive true is a tool error when the flag update fails, and the stop stands", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  world.failUpdate = 1;
  await assert.rejects(
    stop(world, { code: "acme-1", archive: true }),
    (error: unknown) =>
      error instanceof ToolError && /update down/.test(error.message),
  );
  assert.deepEqual(world.interrupts, ["pm1"]);
  assert.equal(sharedState().stopped.has("pm1"), true);
});

await test("list skips hidden PMs, and shows them marked archived when asked", async () => {
  const world = gmWorld([GM, PM1, { ...HIDDEN, id: "pm2" }]);
  assert.deepEqual((await list(world)).split("\n"), [
    "pm-acme-1  pm1  idle  last succeeded",
  ]);
  const shown = await call(
    pmListTool({ ...deps(world), status: quietStatus() }),
    "gm1",
    { archived: true },
  );
  assert.deepEqual(shown.split("\n"), [
    "pm-acme-1  pm1  idle  last succeeded",
    "pm-acme-1  pm2  archived  idle",
  ]);
  const only = gmWorld([GM, HIDDEN]);
  assert.equal(await list(only), "no PM sessions");
});

await test("list with archived true also shows a PM archived in OpenCode, marked as such", async () => {
  const world = gmWorld([GM, PM1, { id: "pm9", metadata: coven("pm", "old") }]);
  world.archived.add("pm9");
  assert.deepEqual((await list(world)).split("\n"), [
    "pm-acme-1  pm1  idle  last succeeded",
  ]);
  const shown = await call(
    pmListTool({ ...deps(world), status: quietStatus() }),
    "gm1",
    { archived: true },
  );
  assert.deepEqual(shown.split("\n"), [
    "pm-acme-1  pm1  idle  last succeeded",
    "pm-old  pm9  archived in OpenCode",
  ]);
});

await test("list is a tool error for an archived option that is not true or false", async () => {
  const world = gmWorld([GM, PM1]);
  await assert.rejects(
    call(pmListTool({ ...deps(world), status: quietStatus() }), "gm1", {
      archived: "yes",
    }),
    ToolError,
  );
});
