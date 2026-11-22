import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import { A, B, C, NOTE, reportText, setup } from "./wait-fixture.ts";

await test("all ends only after the third final report", async () => {
  const w = setup();
  const { box, promise } = w.run();
  await tick();
  w.report(A, "completed", "child A");
  w.report(B, "error", "child B");
  await tick();
  assert.equal(box.result, undefined);
  w.report(C, "cancelled", "child C");
  const result = await promise;
  assert.deepEqual(result.done, [
    { sessionID: A, state: "completed", description: "child A" },
    { sessionID: B, state: "error", description: "child B" },
    { sessionID: C, state: "cancelled", description: "child C" },
  ]);
  assert.deepEqual(result.pending, []);
  assert.equal(result.timedOut, false);
  assert.equal(result.mode, "all");
  assert.equal(result.note, NOTE);
  assert.ok(w.sleeps.every((sleep) => sleep.signal.aborted));
});

await test("any ends on the first, and a second any returns at once", async () => {
  const w = setup();
  const first = w.run({ mode: "any" });
  await tick();
  w.report(B, "completed", "child B");
  const result = await first.promise;
  assert.deepEqual(
    result.done.map((child) => child.sessionID),
    [B],
  );
  assert.deepEqual(result.pending, [A, C]);
  const second = await w.run({ mode: "any" }).promise;
  assert.deepEqual(
    second.done.map((child) => child.sessionID),
    [B],
  );
});

await test("a final report present only in history ends the wait at once", async () => {
  const w = setup([A]);
  w.history.push({
    childID: A,
    source: "subagent",
    text: reportText(A, "completed"),
  });
  const result = await w.run().promise;
  assert.equal(result.done[0]?.state, "completed");
  assert.equal(w.progress.length, 0);
});

await test("an end with no report becomes ended-without-report after 10 s", async () => {
  const w = setup([A]);
  w.start(A);
  const { box, promise } = w.run();
  await tick();
  w.end(A);
  await tick();
  await w.advance(9_999);
  assert.equal(box.result, undefined);
  await w.advance(1);
  const result = await promise;
  assert.deepEqual(result.done, [
    {
      sessionID: A,
      state: "ended-without-report",
      description: "",
      report: "last words",
    },
  ]);
});

await test("a running report then an end keeps waiting until the final report", async () => {
  const w = setup([A]);
  w.start(A);
  const { box, promise } = w.run();
  await tick();
  w.report(A, "running");
  w.end(A);
  for (let i = 0; i < 4; i += 1) {
    await w.advance(15_000);
  }
  assert.equal(box.result, undefined);
  w.report(A, "completed");
  const result = await promise;
  assert.deepEqual(result.done[0], {
    sessionID: A,
    state: "completed",
    description: A,
  });
});

await test("a removed child ends with state removed", async () => {
  const w = setup([A]);
  const { promise } = w.run();
  await tick();
  w.record.observe({ kind: "removed", sessionID: A });
  assert.equal((await promise).done[0]?.state, "removed");
});

await test("timeoutSeconds returns timedOut with the rest pending", async () => {
  const w = setup([A, B]);
  const { box, promise } = w.run({ timeoutSeconds: 30 });
  await tick();
  w.report(A, "completed");
  await tick();
  await w.advance(29_999);
  assert.equal(box.result, undefined);
  await w.advance(1);
  const result = await promise;
  assert.equal(result.timedOut, true);
  assert.equal(result.waitedSeconds, 30);
  assert.deepEqual(result.pending, [B]);
  assert.deepEqual(
    result.done.map((child) => child.sessionID),
    [A],
  );
});

await test("abort rejects with the reason and stops progress and sleeps", async () => {
  const w = setup([A, B]);
  const { promise } = w.run();
  await tick();
  const reason = new Error("stop now");
  w.controller.abort(reason);
  await assert.rejects(promise, (error) => error === reason);
  await w.advance(60_000);
  assert.deepEqual(w.progress, []);
  assert.ok(w.sleeps.every((sleep) => sleep.signal.aborted));
});

await test("an already aborted signal rejects at once", async () => {
  const w = setup([A]);
  const reason = new Error("early");
  w.controller.abort(reason);
  await assert.rejects(w.run().promise, (error) => error === reason);
  assert.equal(w.sleeps.length, 0);
});

await test("progress comes every 30 s with the pending descriptions", async () => {
  const w = setup();
  const { promise } = w.run();
  await tick();
  w.report(A, "completed", "child A");
  w.report(B, "running", "child B");
  w.report(C, "running", "child C");
  await w.advance(29_000);
  assert.deepEqual(w.progress, []);
  await w.advance(1_000);
  await w.advance(30_000);
  const text = "waiting for 2 of 3: child B, child C";
  assert.deepEqual(w.progress, [text, text]);
  w.controller.abort(new Error("done"));
  await assert.rejects(promise);
});
