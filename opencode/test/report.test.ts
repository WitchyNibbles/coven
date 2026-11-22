import assert from "node:assert/strict";
import { test } from "node:test";

import { reportState } from "../src/join/report.ts";

function tag(state: string): string {
  return `<subagent sessionID="ses_c" state="${state}" description="x">\nhi\n</subagent>`;
}

await test("each report state is read from the tag", () => {
  assert.equal(reportState(tag("running")), "running");
  assert.equal(reportState(tag("completed")), "completed");
  assert.equal(reportState(tag("error")), "error");
  assert.equal(reportState(tag("cancelled")), "cancelled");
});

await test("plain text and a missing attribute give undefined", () => {
  assert.equal(reportState("just some text"), undefined);
  assert.equal(
    reportState('<subagent sessionID="ses_c" description="x">hi</subagent>'),
    undefined,
  );
});

await test("an unknown state value gives undefined", () => {
  assert.equal(reportState(tag("paused")), undefined);
});

await test("a description with a quote or a quote and bracket keeps the state", () => {
  for (const state of ["running", "completed"] as const) {
    for (const description of [
      'say "hi" now',
      'ends here"> and more',
      "a > b",
    ]) {
      const text = `<subagent sessionID="ses_c" state="${state}" description="${description}">\nbody\n</subagent>`;
      assert.equal(reportState(text), state, description);
    }
  }
});

await test("a state that does not directly follow sessionID is not read", () => {
  assert.equal(
    reportState(
      '<subagent sessionID="ses_c" description="x" state="error">hi</subagent>',
    ),
    undefined,
  );
});

await test("the first subagent tag decides, not one quoted in the body", () => {
  assert.equal(
    reportState(`${tag("completed")}\n${tag("running")}`),
    "completed",
  );
});
