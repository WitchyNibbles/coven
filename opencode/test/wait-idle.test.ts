import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import { A, B, CALLER, setup } from "./wait-fixture.ts";

await test("an idle child with no report and no end event ends without a report after 10 s", async () => {
  const w = setup([A]);
  const { box, promise } = w.run();
  await tick();
  await w.idle(A);
  assert.equal(w.lastSleepMs(), 10_000);
  await w.advance(9_999);
  assert.equal(box.result, undefined);
  await w.advance(1);
  assert.deepEqual((await promise).done, [
    {
      sessionID: A,
      state: "ended-without-report",
      description: "",
      report: "last words",
    },
  ]);
});

await test("a waitIdle that never settles does not hold off abort", async () => {
  const w = setup([A, B]);
  const { promise } = w.run();
  await tick();
  const reason = new Error("stop");
  w.controller.abort(reason);
  await assert.rejects(promise, (error) => error === reason);
  assert.ok(w.sleeps.every((sleep) => sleep.signal.aborted));
  assert.deepEqual(
    w.idles.map((watch) => [watch.id, watch.signal.aborted]),
    [
      [A, true],
      [B, true],
    ],
  );
});

await test("a final report within the grace after idle is the end", async () => {
  const w = setup([A]);
  const { promise } = w.run();
  await tick();
  await w.idle(A);
  await w.advance(5_000);
  w.report(A, "completed");
  assert.deepEqual((await promise).done, [
    { sessionID: A, state: "completed", description: A },
  ]);
});

await test("a running report after idle keeps the wait going", async () => {
  const w = setup([A]);
  const { box, promise } = w.run();
  await tick();
  await w.idle(A);
  w.report(A, "running");
  for (let i = 0; i < 4; i += 1) {
    await w.advance(15_000);
  }
  assert.equal(box.result, undefined);
  w.report(A, "completed");
  assert.equal((await promise).done[0]?.state, "completed");
});

await test("a failed waitIdle leaves the child to the event stream", async () => {
  const w = setup([A]);
  const { box, promise } = w.run();
  await tick();
  await w.idle(A, new Error("server down"));
  await w.advance(60_000);
  assert.equal(box.result, undefined);
  w.report(A, "error");
  assert.equal((await promise).done[0]?.state, "error");
});

await test("only pending children are watched, and every watch stops on return", async () => {
  const w = setup([A, B]);
  w.report(A, "completed");
  const { promise } = w.run({ timeoutSeconds: 1 });
  await tick();
  await w.advance(1_000);
  assert.equal((await promise).timedOut, true);
  assert.deepEqual(
    w.idles.map((watch) => [watch.id, watch.signal.aborted]),
    [[B, true]],
  );
});

await test("an idle that settles after the call returned changes nothing", async () => {
  const w = setup([A, B]);
  const { promise } = w.run({ mode: "any" });
  await tick();
  w.report(A, "completed");
  await promise;
  await w.idle(B);
  assert.equal(w.record.status(CALLER, B).endedAt, undefined);
});

await test("idle does not restart the grace of a child already ended", async () => {
  const w = setup([A]);
  w.start(A);
  w.end(A);
  const { box, promise } = w.run();
  await tick();
  await w.advance(6_000);
  await w.idle(A);
  await w.advance(3_999);
  assert.equal(box.result, undefined);
  await w.advance(1);
  assert.equal((await promise).done[0]?.state, "ended-without-report");
});
