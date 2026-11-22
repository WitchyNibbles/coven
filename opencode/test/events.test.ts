import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { normalize } from "../src/join/events.ts";

const samples = JSON.parse(
  readFileSync(new URL("./fixtures/events.json", import.meta.url), "utf8"),
) as Record<string, unknown>;
const sample = (name: string): unknown => samples[name];

const A = "ses_dfd034db0ffe4IbnlrWBBAagUF";
const B = "ses_dfd034daeffe6qZ3gvBsO1Nbev";
const C = "ses_dfd034daeffd8o1NsEgnAA6ywq";
const LEAD = "ses_dfd0351b7ffeDuULpN3dOGokkM";

await test("execution.started becomes started", () => {
  assert.deepEqual(normalize(sample("started")), {
    kind: "started",
    sessionID: A,
  });
});

await test("each execution end event becomes ended", () => {
  assert.deepEqual(normalize(sample("succeeded")), {
    kind: "ended",
    sessionID: A,
  });
  assert.deepEqual(normalize(sample("failed")), {
    kind: "ended",
    sessionID: B,
  });
  assert.deepEqual(normalize(sample("interrupted")), {
    kind: "ended",
    sessionID: C,
  });
});

await test("session.deleted becomes removed", () => {
  assert.deepEqual(normalize(sample("deleted")), {
    kind: "removed",
    sessionID: A,
  });
});

await test("a subagent report becomes report with the state from its tag", () => {
  assert.deepEqual(normalize(sample("report")), {
    kind: "report",
    parentID: LEAD,
    childID: A,
    state: "completed",
    description: "child A",
  });
});

await test("the tag state wins over the metadata state", () => {
  assert.deepEqual(normalize(sample("runningReport")), {
    kind: "report",
    parentID: LEAD,
    childID: B,
    state: "running",
    description: "child B",
  });
});

await test("metadata state is the fallback when the tag is unreadable", () => {
  assert.deepEqual(normalize(sample("unreadableReport")), {
    kind: "report",
    parentID: LEAD,
    childID: C,
    state: "error",
    description: "child C",
  });
});

await test("an inbox item that is not a subagent report is ignored", () => {
  assert.equal(normalize(sample("userInbox")), undefined);
});

await test("other events and malformed values are ignored", () => {
  assert.equal(normalize(sample("partDelta")), undefined);
  assert.equal(normalize(sample("textDelta")), undefined);
  assert.equal(normalize(undefined), undefined);
  assert.equal(normalize("session.deleted"), undefined);
  assert.equal(
    normalize({ type: "session.deleted", data: { sessionID: 3 } }),
    undefined,
  );
});
