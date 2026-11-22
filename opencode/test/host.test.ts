import assert from "node:assert/strict";
import { test } from "node:test";

import type { ContextMessage, PluginContext } from "../src/join/context.ts";
import { createHost, sleep } from "../src/join/host.ts";

function hostWith(
  messages: ContextMessage[],
  get: PluginContext["session"]["get"] = () => Promise.reject(new Error("no")),
  wait: PluginContext["session"]["wait"] = () => Promise.resolve(),
) {
  const ctx: PluginContext = {
    event: {
      subscribe: () => ({
        [Symbol.asyncIterator]: () => ({
          next: () => Promise.resolve({ done: true, value: undefined }),
        }),
      }),
    },
    session: {
      get,
      wait,
      context: () => Promise.resolve(messages),
      hook: () => Promise.resolve({}),
    },
    tool: { transform: () => Promise.resolve({}) },
  };
  return createHost(ctx, () => 42);
}

await test("waitIdle asks session.wait for the session with the signal", async () => {
  const calls: unknown[] = [];
  const host = hostWith([], undefined, (input, options) => {
    calls.push([input, options?.signal]);
    return Promise.resolve();
  });
  const { signal } = new AbortController();
  await host.waitIdle("ses_a", signal);
  assert.deepEqual(calls, [[{ sessionID: "ses_a" }, signal]]);
});

await test("history keeps text, source and child id of each message with text", async () => {
  const host = hostWith([
    { type: "user", text: "go" },
    {
      type: "synthetic",
      text: "report",
      metadata: { source: "subagent", childID: "ses_a", agent: "worker" },
    },
    { type: "assistant", content: [{ type: "text", text: "hi" }] },
    { type: "idle" },
  ]);
  assert.deepEqual(await host.history("ses_lead"), [
    { text: "go" },
    { text: "report", source: "subagent", childID: "ses_a" },
  ]);
});

await test("lastAssistantText joins the text parts of the newest assistant message", async () => {
  const host = hostWith([
    { type: "assistant", content: [{ type: "text", text: "old" }] },
    {
      type: "assistant",
      content: [
        { type: "text", text: "first" },
        { type: "tool" },
        { type: "reasoning", text: "hmm" },
        { type: "text", text: "second" },
      ],
    },
    { type: "user", text: "later" },
  ]);
  assert.equal(await host.lastAssistantText("ses_a"), "first\nsecond");
});

await test("lastAssistantText is empty without an assistant message", async () => {
  assert.equal(await hostWith([]).lastAssistantText("ses_a"), "");
});

await test("getSession returns undefined for a not-found error, also as a cause", async () => {
  const missing = Object.assign(new Error("x"), {
    _tag: "Session.NotFoundError",
  });
  assert.equal(
    await hostWith([], () => Promise.reject(missing)).getSession("a"),
    undefined,
  );
  const wrapped = new Error("wrapped", { cause: missing });
  assert.equal(
    await hostWith([], () => Promise.reject(wrapped)).getSession("a"),
    undefined,
  );
});

await test("getSession rethrows any other error", async () => {
  const host = hostWith([], () => Promise.reject(new Error("down")));
  await assert.rejects(host.getSession("a"), /down/);
});

await test("getSession passes the parent through", async () => {
  const host = hostWith([], () =>
    Promise.resolve({ id: "a", parentID: "p", title: "t" }),
  );
  assert.equal((await host.getSession("a"))?.parentID, "p");
});

await test("now is the injected clock", () => {
  assert.equal(hostWith([]).now(), 42);
});

await test("sleep returns at once for an aborted signal and ends early on abort", async () => {
  const done = new AbortController();
  done.abort();
  await sleep(60_000, done.signal);
  const later = new AbortController();
  const pending = sleep(60_000, later.signal);
  later.abort();
  await pending;
});
