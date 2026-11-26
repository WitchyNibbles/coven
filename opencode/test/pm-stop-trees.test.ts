import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { pmStopTool } from "../src/pm/manage.ts";
import { sharedState } from "../src/pm/state.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, call, gmWorld } from "./pm-gm-world.ts";
import { deps } from "./pm-manage-kit.ts";

const PM1: SessionLike = {
  id: "pm1",
  metadata: coven("pm", "acme-1"),
  outcome: "succeeded",
};

/** The stop tool with a tree stopper that answers `swept` and notes which session it was asked for. */
function stopWith(
  swept: { killed: number; skipped: number } | Error,
  asked: string[],
) {
  const world = gmWorld([GM, PM1]);
  const tool = pmStopTool({
    ...deps(world),
    trees: (sessionID) => {
      asked.push(sessionID);
      return swept instanceof Error
        ? Promise.reject(swept)
        : Promise.resolve(swept);
    },
  });
  return { world, run: (input: unknown) => call(tool, "gm1", input) };
}

await test("stop reports how many isolation trees it killed, for the PM's own session", async () => {
  sharedState().stopped.clear();
  const asked: string[] = [];
  const { world, run } = stopWith({ killed: 2, skipped: 0 }, asked);
  assert.equal(
    await run({ code: "acme-1" }),
    "stopped pm-acme-1; killed 2 isolation tree(s)",
  );
  assert.deepEqual(asked, ["pm1"]);
  assert.deepEqual(world.interrupts, ["pm1"]);
});

await test("stop says how many stale records it skipped when there are any", async () => {
  sharedState().stopped.clear();
  const { run } = stopWith({ killed: 0, skipped: 3 }, []);
  assert.equal(
    await run({ code: "acme-1" }),
    "stopped pm-acme-1; killed 0 isolation tree(s); skipped 3 stale record(s)",
  );
});

await test("stop with archive keeps its text and adds the counts after it", async () => {
  sharedState().stopped.clear();
  const { run } = stopWith({ killed: 1, skipped: 1 }, []);
  assert.equal(
    await run({ code: "acme-1", archive: true }),
    "stopped pm-acme-1; hidden from coven_pm_list; the session is not archived in OpenCode; killed 1 isolation tree(s); skipped 1 stale record(s)",
  );
});

await test("the trees are stopped after the interrupt, and a failed interrupt stops none", async () => {
  sharedState().stopped.clear();
  const asked: string[] = [];
  const { world, run } = stopWith({ killed: 1, skipped: 0 }, asked);
  world.failInterrupt = true;
  await assert.rejects(run({ code: "acme-1" }), /interrupt down/);
  assert.deepEqual(asked, []);
});

await test("a tree sweep that fails is a tool error that says the PM was interrupted", async () => {
  sharedState().stopped.clear();
  const { world, run } = stopWith(new Error("proc unreadable"), []);
  await assert.rejects(
    run({ code: "acme-1" }),
    /interrupted pm-acme-1 but could not stop its isolation trees: proc unreadable/,
  );
  assert.deepEqual(world.interrupts, ["pm1"]);
});

await test("a stop with no tree stopper (no isolation wired) keeps the plain text", async () => {
  sharedState().stopped.clear();
  const world = gmWorld([GM, PM1]);
  const out = await call(pmStopTool(deps(world)), "gm1", { code: "acme-1" });
  assert.equal(out, "stopped pm-acme-1");
});
