import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import { EVENT_TYPES } from "../src/join/events.ts";
import { inputSchema, InputError } from "../src/join/input.ts";
import { NotAChildError, UnknownSessionError } from "../src/join/wait.ts";
import plugin from "../src/plugin.ts";
import { KID, LEAD, boot, report, started } from "./plugin-fixture.ts";

await test("the plugin has the id coven.join and a setup", () => {
  assert.equal(plugin.id, "coven.join");
  assert.equal(typeof plugin.setup, "function");
});

await test("setup registers await_agents with the input schema and no codemode", async () => {
  const p = await boot();
  assert.equal(p.tools.length, 1);
  const [tool] = p.tools;
  assert.equal(tool?.name, "await_agents");
  assert.equal(tool?.input, inputSchema);
  assert.deepEqual(tool?.options, { codemode: false });
  const description = tool?.description ?? "";
  assert.match(description, /final report/);
  assert.match(description, /states only/);
  assert.match(description, /<subagent> messages/);
  assert.match(description, /only the ids still pending/);
  assert.match(description, /server restart/);
  assert.match(description, /best effort/);
  p.cleanup();
});

await test("setup subscribes with exactly the join event types", async () => {
  const p = await boot();
  assert.equal(p.subscriptions.length, 1);
  assert.deepEqual(p.subscriptions[0]?.types, EVENT_TYPES);
  assert.deepEqual(
    [...EVENT_TYPES].sort(),
    [
      "session.deleted",
      "session.execution.failed",
      "session.execution.interrupted",
      "session.execution.started",
      "session.execution.succeeded",
      "session.inbox.enqueued",
    ].sort(),
  );
  p.cleanup();
});

await test("the reminder appears only for a session with running children", async () => {
  const p = await boot();
  assert.deepEqual(p.reminder(LEAD), []);
  await p.emit(started(KID));
  assert.deepEqual(p.reminder(LEAD), [
    "1 subagent is still running: kid (ses_kid). Call await_agents with their ids before you finish or report.",
  ]);
  assert.deepEqual(p.reminder("ses_other"), []);
  p.cleanup();
});

await test("the reminder stops once the child has sent its final report", async () => {
  const p = await boot();
  await p.emit(started(KID));
  await p.emit(report(LEAD, KID, "completed"));
  assert.deepEqual(p.reminder(LEAD), []);
  p.cleanup();
});

await test("a session seen in two started events is looked up once", async () => {
  const p = await boot();
  await p.emit(started(KID));
  await p.emit(started(KID));
  assert.deepEqual(p.lookups, [KID]);
  p.cleanup();
});

await test("a failed lookup is cached and leaves the session unlinked", async () => {
  const p = await boot();
  p.sessions.set("ses_x", new Error("down"));
  await p.emit(started("ses_x"));
  await p.emit(started("ses_x"));
  assert.deepEqual(p.lookups, ["ses_x"]);
  assert.deepEqual(p.reminder(LEAD), []);
  p.cleanup();
});

await test("a session without a parent is not a child", async () => {
  const p = await boot();
  p.sessions.set(LEAD, { id: LEAD });
  await p.emit(started(LEAD));
  assert.deepEqual(p.reminder(LEAD), []);
  p.cleanup();
});

await test("a child without a title is listed by its id", async () => {
  const p = await boot();
  p.sessions.set(KID, { id: KID, parentID: LEAD });
  await p.emit(started(KID));
  assert.deepEqual(p.reminder(LEAD), [
    "1 subagent is still running: ses_kid. Call await_agents with their ids before you finish or report.",
  ]);
  p.cleanup();
});

await test("await_agents answers from the record when the report arrives", async () => {
  const p = await boot();
  await p.emit(started(KID));
  const { result, progress } = p.call({ ids: [KID] });
  await tick();
  await p.emit(report(LEAD, KID, "completed"));
  const content = JSON.parse((await result).content ?? "{}") as {
    done: { sessionID: string; state: string }[];
    pending: string[];
    timedOut: boolean;
  };
  assert.deepEqual(
    content.done.map((child) => child.state),
    ["completed"],
  );
  assert.equal(content.done[0]?.sessionID, KID);
  assert.deepEqual(content.pending, []);
  assert.equal(content.timedOut, false);
  assert.deepEqual(progress, []);
  p.cleanup();
});

await test("await_agents reads reports from the conversation history", async () => {
  const p = await boot();
  p.messages.set(LEAD, [
    { type: "user", text: "go" },
    {
      type: "synthetic",
      text: '<subagent sessionID="ses_kid" state="error" description="kid">\nboom\n</subagent>',
      metadata: { source: "subagent", childID: KID },
    },
  ]);
  const { result } = p.call({ ids: [KID] });
  const content = JSON.parse((await result).content ?? "{}") as {
    done: { state: string }[];
  };
  assert.deepEqual(
    content.done.map((child) => child.state),
    ["error"],
  );
  p.cleanup();
});

await test("await_agents rejects bad input with the input error", async () => {
  const p = await boot();
  await assert.rejects(p.call({ ids: [] }).result, InputError);
  await assert.rejects(p.call({ ids: [LEAD] }).result, /calling session/);
  p.cleanup();
});

await test("await_agents rejects an unknown session with its message", async () => {
  const p = await boot();
  await assert.rejects(p.call({ ids: ["ses_nope"] }).result, (error) => {
    assert.ok(error instanceof UnknownSessionError);
    assert.equal(error.message, "session ses_nope does not exist");
    return true;
  });
  p.cleanup();
});

await test("await_agents rejects a session that is not a child", async () => {
  const p = await boot();
  p.sessions.set("ses_far", { id: "ses_far", parentID: "ses_elsewhere" });
  await assert.rejects(p.call({ ids: ["ses_far"] }).result, NotAChildError);
  p.cleanup();
});

await test("await_agents reports a failed lookup as an error", async () => {
  const p = await boot();
  p.sessions.set("ses_x", new Error("down"));
  await assert.rejects(
    p.call({ ids: ["ses_x"] }).result,
    /could not look up session ses_x: down/,
  );
  p.cleanup();
});

await test("an abort propagates out of await_agents", async () => {
  const p = await boot();
  await p.emit(started(KID));
  const controller = new AbortController();
  const { result } = p.call({ ids: [KID] }, LEAD, controller.signal);
  await tick();
  controller.abort();
  await assert.rejects(result, (error) => {
    assert.equal(controller.signal.reason, error);
    return true;
  });
  p.cleanup();
});
