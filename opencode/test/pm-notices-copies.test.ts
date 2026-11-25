import assert from "node:assert/strict";
import { test } from "node:test";

import { NoticeTracker } from "../src/pm/notices.ts";
import { end, rig, start } from "./pm-notices-rig.ts";
import type { Rig } from "./pm-notices-rig.ts";

function otherLocation(from: Rig): NoticeTracker {
  return new NoticeTracker("/work/other", {
    session: from.world,
    state: from.state,
    lastText: () => Promise.resolve("done"),
    startedAt: 0,
    log: (line) => from.logs.push(line),
  });
}

await test("another location's copy does not clear the marks of a pm it does not own", async () => {
  const mine = rig();
  mine.state.messaged.add("pm1");
  mine.state.stopped.add("pm1");
  await otherLocation(mine).onEvent(start("pm1"));
  assert.deepEqual([...mine.state.messaged], ["pm1"]);
  assert.deepEqual([...mine.state.stopped], ["pm1"]);
});

await test("a start seen by a foreign copy first does not cause a notice at the owner's end", async () => {
  const mine = rig();
  mine.state.messaged.add("pm1");
  await otherLocation(mine).onEvent(start("pm1"));
  await mine.tracker.onEvent(end("pm1"));
  assert.deepEqual(mine.world.prompts, []);
});

await test("a start whose session lookup fails clears the marks, so the turn end still notices", async () => {
  const mine = rig();
  mine.state.stopped.add("pm1");
  const get = mine.world.get.bind(mine.world);
  mine.world.get = () => Promise.reject(new Error("get down"));
  await mine.tracker.onEvent(start("pm1"));
  mine.world.get = get;
  await mine.tracker.onEvent(end("pm1"));
  assert.equal(mine.world.prompts.length, 1);
  assert.deepEqual([...mine.state.stopped], []);
});

await test("the owning copy still clears the marks on a start", async () => {
  const mine = rig();
  mine.state.messaged.add("pm1");
  await otherLocation(mine).onEvent(start("pm1"));
  await mine.tracker.onEvent(start("pm1"));
  assert.deepEqual([...mine.state.messaged], []);
});
