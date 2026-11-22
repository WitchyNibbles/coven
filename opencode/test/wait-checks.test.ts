import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import {
  awaitAgents,
  NotAChildError,
  UnknownSessionError,
} from "../src/join/wait.ts";
import type { Host } from "../src/join/wait.ts";
import { A, B, CALLER, reportText, setup } from "./wait-fixture.ts";

await test("a report fed by the 15 s history re-read ends the wait", async () => {
  const w = setup([A]);
  const { box, promise } = w.run();
  await tick();
  w.history.push({
    childID: A,
    source: "subagent",
    text: reportText(A, "error"),
  });
  await w.advance(14_000);
  assert.equal(box.result, undefined);
  await w.advance(1_000);
  const result = await promise;
  assert.equal(result.done[0]?.state, "error");
});

await test("history never replays an old report over a newer start", async () => {
  const w = setup([A]);
  w.start(A);
  w.report(A, "completed");
  w.start(A);
  w.history.push(
    { childID: A, source: "subagent", text: reportText(A, "completed") },
    {
      childID: "ses_x",
      source: "subagent",
      text: reportText("ses_x", "completed"),
    },
    { childID: A, source: "user", text: reportText(A, "completed") },
  );
  const { box, promise } = w.run();
  await w.advance(15_000);
  assert.equal(box.result, undefined);
  w.report(A, "cancelled");
  assert.equal((await promise).done[0]?.state, "cancelled");
});

await test("history feeds each report not yet seen, in order", async () => {
  const w = setup([A]);
  w.history.push(
    { childID: A, source: "subagent", text: reportText(A, "running") },
    { childID: A, source: "subagent", text: "no tag here" },
    { childID: A, source: "subagent", text: reportText(A, "completed") },
  );
  const result = await w.run().promise;
  assert.equal(result.done[0]?.state, "completed");
  assert.equal(w.record.reportCount(CALLER, A), 2);
});

await test("an unknown id throws UnknownSessionError naming it", async () => {
  const w = setup([A]);
  await assert.rejects(
    w.run({ ids: [A, "ses_gone"] }).promise,
    (error) =>
      error instanceof UnknownSessionError &&
      error.name === "UnknownSessionError" &&
      error.message.includes("ses_gone"),
  );
});

await test("a session with another parent throws NotAChildError naming only it", async () => {
  const w = setup([A, B]);
  w.sessions.set(B, { id: B, parentID: "ses_other" });
  await assert.rejects(w.run({ ids: [A, B] }).promise, (error) => {
    assert.ok(error instanceof NotAChildError);
    assert.equal(error.name, "NotAChildError");
    assert.equal(
      error.message,
      `session ${B} is not a child of the calling session`,
    );
    return true;
  });
});

await test("a failed lookup is an error naming the id", async () => {
  const w = setup([A]);
  const host: Host = {
    getSession: () => Promise.reject(new Error("boom")),
    history: () => Promise.resolve([]),
    lastAssistantText: () => Promise.resolve(""),
    now: () => 0,
    sleep: () => Promise.resolve(),
  };
  const call = {
    callerID: CALLER,
    signal: w.controller.signal,
    progress: () => {},
  };
  await assert.rejects(
    awaitAgents({ ids: [A], mode: "all" }, call, w.record, host),
    (error) =>
      error instanceof Error &&
      error.message.includes(A) &&
      error.message.includes("boom"),
  );
});
