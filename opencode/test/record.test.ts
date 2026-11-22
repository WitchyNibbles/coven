import assert from "node:assert/strict";
import { test } from "node:test";

import { JoinRecord } from "../src/join/record.ts";
import type { Observed } from "../src/join/record.ts";
import type { ReportState } from "../src/join/report.ts";

const HOUR = 3_600_000;

function setup(): { record: JoinRecord; clock: { now: number } } {
  const clock = { now: 1_000 };
  return { record: new JoinRecord(() => clock.now), clock };
}

function report(state: ReportState, description = "child A"): Observed {
  return {
    kind: "report",
    parentID: "ses_p",
    childID: "ses_c",
    state,
    description,
  };
}

const started: Observed = { kind: "started", sessionID: "ses_c" };
const ended: Observed = { kind: "ended", sessionID: "ses_c" };

await test("a completed report ends the child", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  record.observe(report("completed"));
  assert.equal(record.status("ses_p", "ses_c").end, "completed");
});

await test("each final state is the end", () => {
  for (const state of ["completed", "error", "cancelled"] as const) {
    const { record } = setup();
    record.observe(started);
    record.observe(report(state));
    assert.equal(record.status("ses_p", "ses_c").end, state);
  }
});

await test("a start after the report clears the end", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  record.observe(report("completed"));
  record.observe(started);
  assert.equal(record.status("ses_p", "ses_c").end, undefined);
  assert.deepEqual(
    record.running("ses_p").map((child) => child.sessionID),
    ["ses_c"],
  );
});

await test("a running report after the start is not an end, even after ended", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  record.observe(report("running"));
  record.observe(ended);
  const status = record.status("ses_p", "ses_c");
  assert.equal(status.end, undefined);
  assert.equal(status.endedAt, undefined);
});

await test("ended with no report sets endedAt to now", () => {
  const { record, clock } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  clock.now = 5_000;
  record.observe(ended);
  const status = record.status("ses_p", "ses_c");
  assert.equal(status.end, undefined);
  assert.equal(status.endedAt, 5_000);
});

await test("a report from before the newest start does not hide ended", () => {
  const { record, clock } = setup();
  record.observe(started);
  record.observe(report("completed"));
  record.observe(started);
  clock.now = 7_000;
  record.observe(ended);
  const status = record.status("ses_p", "ses_c");
  assert.equal(status.end, undefined);
  assert.equal(status.endedAt, 7_000);
});

await test("a final report after ended replaces endedAt with the end", () => {
  const { record } = setup();
  record.observe(started);
  record.observe(ended);
  record.observe(report("error"));
  const status = record.status("ses_p", "ses_c");
  assert.equal(status.end, "error");
  assert.equal(status.endedAt, undefined);
});

await test("a start after ended clears endedAt", () => {
  const { record } = setup();
  record.observe(started);
  record.observe(ended);
  record.observe(started);
  assert.equal(record.status("ses_p", "ses_c").endedAt, undefined);
});

await test("removed is final at once", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  record.observe({ kind: "removed", sessionID: "ses_c" });
  assert.equal(record.status("ses_p", "ses_c").end, "removed");
  assert.deepEqual(record.running("ses_p"), []);
});

await test("running lists linked children with no end", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_a", "child A");
  record.linkChild("ses_p", "ses_b", "child B");
  record.linkChild("ses_q", "ses_z", "other parent");
  record.observe({ kind: "started", sessionID: "ses_a" });
  record.observe({ kind: "started", sessionID: "ses_b" });
  record.observe({
    kind: "report",
    parentID: "ses_p",
    childID: "ses_a",
    state: "completed",
    description: "child A",
  });
  assert.deepEqual(record.running("ses_p"), [
    { sessionID: "ses_b", description: "child B" },
  ]);
});

await test("a report for an unlinked child links it to that parent", () => {
  const { record } = setup();
  record.observe(report("running", "from the report"));
  assert.deepEqual(record.status("ses_p", "ses_c"), {
    sessionID: "ses_c",
    description: "from the report",
  });
  assert.equal(record.running("ses_p").length, 1);
  assert.deepEqual(record.running("ses_q"), []);
});

await test("the description is the newer of the link and the report", () => {
  const { record } = setup();
  record.linkChild("ses_p", "ses_c", "first");
  record.observe(report("running", "second"));
  assert.equal(record.status("ses_p", "ses_c").description, "second");
  record.linkChild("ses_p", "ses_c", "third");
  assert.equal(record.status("ses_p", "ses_c").description, "third");
});

await test("an unknown child has an empty description and no end", () => {
  const { record } = setup();
  assert.deepEqual(record.status("ses_p", "ses_none"), {
    sessionID: "ses_none",
    description: "",
  });
});

await test("a child linked to another parent is unknown to this one", () => {
  const { record } = setup();
  record.linkChild("ses_q", "ses_c", "theirs");
  record.observe(report("completed"));
  assert.deepEqual(record.status("ses_x", "ses_c"), {
    sessionID: "ses_c",
    description: "",
  });
});

await test("forget drops a parent 24 hours and 1 ms after its last event", () => {
  const { record, clock } = setup();
  record.linkChild("ses_p", "ses_c", "child A");
  record.observe(started);
  clock.now += 24 * HOUR;
  record.forget();
  assert.equal(record.running("ses_p").length, 1);
  clock.now += 1;
  record.forget();
  assert.deepEqual(record.running("ses_p"), []);
  assert.equal(record.status("ses_p", "ses_c").description, "");
});

await test("a newer event keeps the parent through forget", () => {
  const { record, clock } = setup();
  record.linkChild("ses_p", "ses_a", "child A");
  clock.now += 20 * HOUR;
  record.linkChild("ses_p", "ses_b", "child B");
  clock.now += 10 * HOUR;
  record.forget();
  assert.equal(record.running("ses_p").length, 2);
});

await test("forget also drops a child that was never linked", () => {
  const { record, clock } = setup();
  record.observe(started);
  clock.now += 24 * HOUR + 1;
  record.forget();
  record.linkChild("ses_p", "ses_c", "child A");
  assert.equal(record.running("ses_p").length, 1);
  assert.equal(record.status("ses_p", "ses_c").endedAt, undefined);
});

await test("onChange fires after a change and not after unsubscribe", () => {
  const { record, clock } = setup();
  let calls = 0;
  const off = record.onChange(() => {
    calls += 1;
  });
  record.linkChild("ses_p", "ses_c", "child A");
  assert.equal(calls, 1);
  record.observe(started);
  assert.equal(calls, 2);
  clock.now += 24 * HOUR + 1;
  record.forget();
  assert.equal(calls, 3);
  off();
  record.observe(started);
  record.linkChild("ses_p", "ses_d", "child D");
  assert.equal(calls, 3);
});

await test("forget with nothing to drop does not fire onChange", () => {
  const { record } = setup();
  let calls = 0;
  record.onChange(() => {
    calls += 1;
  });
  record.forget();
  assert.equal(calls, 0);
});

await test("a listener sees the state already changed", () => {
  const { record } = setup();
  let seen: string | undefined = "unset";
  record.onChange(() => {
    seen = record.status("ses_p", "ses_c").end;
  });
  record.observe(report("completed"));
  assert.equal(seen, "completed");
});
