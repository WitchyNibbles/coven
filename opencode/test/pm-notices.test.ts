import assert from "node:assert/strict";
import { test } from "node:test";

import { coven } from "./pm-fake-session.ts";
import { HERE, end, rig, sent, start } from "./pm-notices-rig.ts";

await test("a pm turn with no message queues one notice into the GM with the exact text", async () => {
  const { world, tracker } = rig();
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, [
    {
      sessionID: "gm1",
      text: "[coven notice] pm-acme ended a turn (succeeded) without a message. Last line: all done",
      delivery: "queue",
    },
  ]);
});

await test("the last line is cut to 200 characters", async () => {
  const { world, tracker } = rig([], () => Promise.resolve("x".repeat(500)));
  await tracker.onEvent(end("pm1"));
  assert.equal(sent(world)[0]?.split("Last line: ")[1], "x".repeat(200));
});

await test("an unreadable last message still gives the notice", async () => {
  const { world, tracker } = rig([], () => Promise.reject(new Error("down")));
  await tracker.onEvent(end("pm1"));
  assert.match(sent(world)[0] ?? "", /Last line: \(unreadable\)$/);
});

await test("no notice when the pm sent to the GM in that turn", async () => {
  const { world, tracker, state } = rig();
  state.messaged.add("pm1");
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
});

await test("no notice for a child (parentID)", async () => {
  const { world, tracker } = rig([
    {
      id: "kid1",
      parentID: "pm1",
      location: { directory: HERE },
      metadata: coven("pm", "acme"),
    },
  ]);
  await tracker.onEvent(end("kid1"));
  assert.deepEqual(world.prompts, []);
});

await test("no notice for a session in another directory", async () => {
  const { world, tracker } = rig([], undefined, "/work/other");
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
});

await test("no notice for a session that is not a pm", async () => {
  const { world, tracker } = rig([
    { id: "bare", location: { directory: HERE } },
    { id: "gm2", location: { directory: HERE }, metadata: coven("gm") },
  ]);
  await tracker.onEvent(end("bare"));
  await tracker.onEvent(end("gm2"));
  assert.deepEqual(world.prompts, []);
});

await test("no notice after coven_pm_stop", async () => {
  const { world, tracker, state } = rig();
  state.stopped.add("pm1");
  await tracker.onEvent(end("pm1", "interrupted"));
  assert.deepEqual(world.prompts, []);
});

await test("no notice while the pm has a busy child", async () => {
  const { world, tracker } = rig([
    {
      id: "kid1",
      parentID: "pm1",
      time: { created: 1, busy: 900, idle: 100 },
    },
  ]);
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
});

await test("a child that is idle does not hold the notice back", async () => {
  const { world, tracker } = rig([
    {
      id: "kid1",
      parentID: "pm1",
      time: { created: 1, busy: 100, idle: 900 },
    },
    { id: "kid2", parentID: "pm1" },
  ]);
  await tracker.onEvent(end("pm1"));
  assert.equal(world.prompts.length, 1);
});

await test("a busy grandchild holds the notice back too", async () => {
  const { world, tracker } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 100, idle: 900 } },
    { id: "kid2", parentID: "kid1", time: { busy: 900 } },
  ]);
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
});

await test("a failed turn gets a notice even after a send", async () => {
  const { world, tracker, state } = rig();
  state.messaged.add("pm1");
  await tracker.onEvent(end("pm1", "failed"));
  assert.equal(
    sent(world)[0],
    "[coven notice] pm-acme ended a turn (failed) after its message. Last line: all done",
  );
});

await test("a failed turn gets a notice even with a busy child", async () => {
  const { world, tracker } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 900 } },
  ]);
  await tracker.onEvent(end("pm1", "failed"));
  assert.match(sent(world)[0] ?? "", /\(failed\) without a message/);
});

await test("a failed turn after the GM stopped the pm gets no notice", async () => {
  const { world, tracker, state } = rig();
  state.stopped.add("pm1");
  await tracker.onEvent(end("pm1", "failed"));
  assert.deepEqual(world.prompts, []);
});

await test("failed and interrupted outcomes are named", async () => {
  const { world, tracker } = rig();
  await tracker.onEvent(end("pm1", "failed"));
  await tracker.onEvent(end("pm1", "interrupted"));
  assert.deepEqual(
    sent(world).map((text) => /\((\w+)\)/.exec(text)?.[1]),
    ["failed", "interrupted"],
  );
});

await test("a missing GM writes the notice to the serve log", async () => {
  const { world, tracker, logs } = rig();
  world.sessions.delete("gm1");
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
  assert.equal(logs.length, 1);
  assert.match(logs[0] ?? "", /no GM session/);
  assert.match(logs[0] ?? "", /\[coven notice\] pm-acme ended a turn/);
});

await test("a notice that cannot be sent is logged", async () => {
  const { world, tracker, logs } = rig();
  world.failPrompt = true;
  await tracker.onEvent(end("pm1"));
  assert.equal(logs.length, 1);
  assert.match(logs[0] ?? "", /prompt down/);
});

await test("a session that cannot be read is logged, not guessed", async () => {
  const { world, tracker, logs } = rig();
  world.get = () => Promise.reject(new Error("get down"));
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
  assert.match(logs[0] ?? "", /pm1.*get down/);
});

await test("a busy-child read that fails still gives the notice, and is logged", async () => {
  const { world, tracker, logs } = rig();
  const list = world.list.bind(world);
  world.list = (input) =>
    input.parentID === undefined
      ? list(input)
      : Promise.reject(new Error("list down"));
  await tracker.onEvent(end("pm1"));
  assert.equal(world.prompts.length, 1);
  assert.match(logs[0] ?? "", /cannot read the children of pm-acme.*list down/);
});

await test("a turn start clears the stop and message marks of that session only", async () => {
  const { tracker, state } = rig();
  state.messaged.add("pm1").add("pm2");
  state.stopped.add("pm1").add("pm2");
  await tracker.onEvent(start("pm1"));
  assert.deepEqual([...state.messaged], ["pm2"]);
  assert.deepEqual([...state.stopped], ["pm2"]);
});

await test("a stop of an idle pm does not suppress the next real notice", async () => {
  const { world, tracker, state } = rig();
  state.stopped.add("pm1");
  await tracker.onEvent(start("pm1"));
  await tracker.onEvent(end("pm1"));
  assert.equal(world.prompts.length, 1);
});

await test("the marks are read before the next turn can clear them", async () => {
  const { world, tracker, state } = rig();
  state.messaged.add("pm1");
  const ending = tracker.onEvent(end("pm1"));
  await tracker.onEvent(start("pm1"));
  await ending;
  assert.deepEqual(world.prompts, []);
});

await test("events that are not turn events change nothing", async () => {
  const { world, tracker } = rig();
  await tracker.onEvent({
    type: "session.deleted",
    data: { sessionID: "pm1" },
  });
  await tracker.onEvent({ type: "session.execution.succeeded", data: {} });
  await tracker.onEvent("nonsense");
  await tracker.onEvent(undefined);
  assert.deepEqual(world.prompts, []);
});

await test("a busy child holds the notice back and says so in the serve log", async () => {
  const { world, tracker, logs } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 900 } },
  ]);
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
  assert.equal(logs.length, 1);
  assert.match(logs[0] ?? "", /pm-acme.*kid1.*busy/);
});

await test("a child busy since before this plugin started does not hold a notice back", async () => {
  const { world, tracker, logs } = rig(
    [{ id: "kid1", parentID: "pm1", time: { busy: 900 } }],
    undefined,
    HERE,
    1000,
  );
  await tracker.onEvent(end("pm1"));
  assert.equal(world.prompts.length, 1);
  assert.deepEqual(logs, []);
});

await test("a child that turned busy after the plugin started still does", async () => {
  const { world, tracker } = rig(
    [{ id: "kid1", parentID: "pm1", time: { busy: 1500 } }],
    undefined,
    HERE,
    1000,
  );
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
});

await test("one pm turn end reads the session once", async () => {
  const { world, tracker } = rig();
  const reads: string[] = [];
  const get = world.get.bind(world);
  world.get = (input) => {
    reads.push(input.sessionID);
    return get(input);
  };
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(
    reads.filter((id) => id === "pm1"),
    ["pm1"],
  );
  assert.equal(world.prompts.length, 1);
});

await test("a busy first child ends the walk: no deeper level is read", async () => {
  const { world, tracker, logs } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 900 } },
    { id: "kid2", parentID: "kid1", time: { busy: 900 } },
  ]);
  const asked: (string | undefined)[] = [];
  const list = world.list.bind(world);
  world.list = (input) => {
    asked.push(input.parentID);
    return list(input);
  };
  await tracker.onEvent(end("pm1"));
  assert.deepEqual(world.prompts, []);
  assert.deepEqual(
    asked.filter((id) => id !== undefined),
    ["pm1"],
  );
  assert.match(logs[0] ?? "", /held back for pm-acme: child kid1 is busy/);
});
