import assert from "node:assert/strict";
import { test } from "node:test";

import type { Decide } from "./harness/fake-model.ts";
import { replyFor, strandedText } from "./harness/fake-model.ts";

const PING = { name: "coven_ping", args: { note: "hi" } };
const TOOL = { type: "function", function: { name: "coven_ping" } };

const decide: Decide = () => ({ kind: "calls", calls: [PING] });
const talk: Decide = () => ({ kind: "text", text: "done" });

function body(extra: Record<string, unknown>): string {
  return JSON.stringify({
    messages: [{ role: "user", content: "go" }],
    ...extra,
  });
}

await test("strandedText joins one tool_call tag per call with a newline", () => {
  const other = { name: "coven_pong", args: {} };
  assert.equal(
    strandedText([PING, other]),
    '<tool_call>{"name":"coven_ping","arguments":{"note":"hi"}}</tool_call>\n' +
      '<tool_call>{"name":"coven_pong","arguments":{}}</tool_call>',
  );
});

await test("litellm mode strands the call when tools come without tool_choice", () => {
  const { reply, recorded } = replyFor(
    body({ tools: [TOOL] }),
    "litellm",
    decide,
  );
  assert.deepEqual(reply, { kind: "stranded", text: strandedText([PING]) });
  assert.equal(recorded.tools, 1);
  assert.equal(recorded.hasToolChoice, false);
});

await test("litellm mode gives the calls reply when tool_choice is present", () => {
  const { reply, recorded } = replyFor(
    body({ tools: [TOOL], tool_choice: "auto" }),
    "litellm",
    decide,
  );
  assert.deepEqual(reply, { kind: "calls", calls: [PING] });
  assert.equal(recorded.hasToolChoice, true);
  assert.equal(recorded.toolChoice, "auto");
});

await test("a null tool_choice still counts as the key being present", () => {
  const { reply, recorded } = replyFor(
    body({ tools: [TOOL], tool_choice: null }),
    "litellm",
    decide,
  );
  assert.deepEqual(reply, { kind: "calls", calls: [PING] });
  assert.equal(recorded.hasToolChoice, true);
  assert.equal(recorded.toolChoice, null);
});

await test("plain mode gives the calls reply with and without tool_choice", () => {
  const bare = replyFor(body({ tools: [TOOL] }), "plain", decide);
  const auto = replyFor(
    body({ tools: [TOOL], tool_choice: "auto" }),
    "plain",
    decide,
  );
  assert.deepEqual(bare.reply, { kind: "calls", calls: [PING] });
  assert.deepEqual(auto.reply, { kind: "calls", calls: [PING] });
  assert.equal(bare.recorded.tools, 1);
  assert.equal(bare.recorded.hasToolChoice, false);
  assert.equal(auto.recorded.hasToolChoice, true);
});

await test("a request with no tools records tools 0 and is never stranded", () => {
  const { reply, recorded } = replyFor(body({}), "litellm", decide);
  assert.deepEqual(reply, { kind: "calls", calls: [PING] });
  assert.equal(recorded.tools, 0);
  assert.equal(recorded.hasToolChoice, false);
});

await test("a text reply is never stranded", () => {
  const { reply } = replyFor(body({ tools: [TOOL] }), "litellm", talk);
  assert.deepEqual(reply, { kind: "text", text: "done" });
});

await test("decide sees the messages and the tool names", () => {
  let seen: { messages: number; names: readonly string[] } | undefined;
  const spy: Decide = (messages, names) => {
    seen = { messages: messages.length, names };
    return { kind: "text", text: "ok" };
  };
  replyFor(body({ tools: [TOOL] }), "plain", spy);
  assert.deepEqual(seen, { messages: 1, names: ["coven_ping"] });
});

await test("stream is recorded as the fake will honour it", () => {
  assert.equal(replyFor(body({}), "plain", talk).recorded.stream, true);
  assert.equal(
    replyFor(body({ stream: false }), "plain", talk).recorded.stream,
    false,
  );
});

await test("an unparsable body is a request with no tools", () => {
  const { recorded } = replyFor("not json", "litellm", talk);
  assert.equal(recorded.tools, 0);
  assert.equal(recorded.hasToolChoice, false);
});
