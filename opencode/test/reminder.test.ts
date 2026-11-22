import assert from "node:assert/strict";
import { test } from "node:test";

import { reminderText } from "../src/join/reminder.ts";

await test("reminderText is undefined for no running child", () => {
  assert.equal(reminderText([]), undefined);
});

await test("reminderText names one running child", () => {
  assert.equal(
    reminderText([{ sessionID: "ses_a", description: "child A" }]),
    "1 subagent is still running: child A (ses_a). Call await_agents with their ids before you finish or report.",
  );
});

await test("reminderText names two running children", () => {
  assert.equal(
    reminderText([
      { sessionID: "ses_b", description: "child B" },
      { sessionID: "ses_c", description: "child C" },
    ]),
    "2 subagents are still running: child B (ses_b), child C (ses_c). Call await_agents with their ids before you finish or report.",
  );
});

await test("reminderText falls back to the id for a child without description", () => {
  assert.equal(
    reminderText([{ sessionID: "ses_a", description: "" }]),
    "1 subagent is still running: ses_a. Call await_agents with their ids before you finish or report.",
  );
});
