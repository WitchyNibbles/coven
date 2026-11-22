import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChatMessage } from "./harness/chat.ts";
import { decidePing } from "./e2e/runtime/ping-script.ts";

const ASKED: ChatMessage = {
  role: "assistant",
  tool_calls: [{ id: "call_1", function: { name: "coven_ping" } }],
};

await test("a request with no tools is the title agent's", () => {
  assert.deepEqual(decidePing([], []), { kind: "text", text: "title" });
});

await test("a request without coven_ping says the tool is missing", () => {
  assert.deepEqual(decidePing([], ["read", "edit"]), {
    kind: "text",
    text: "NO PING TOOL",
  });
});

await test("the first request with coven_ping asks for it", () => {
  assert.deepEqual(decidePing([], ["read", "coven_ping"]), {
    kind: "calls",
    calls: [{ name: "coven_ping", args: {} }],
  });
});

await test("once the tool has answered, the model reports its result", () => {
  const answered: ChatMessage[] = [
    ASKED,
    { role: "tool", tool_call_id: "call_1", content: [{ text: "pong" }] },
  ];
  assert.deepEqual(decidePing(answered, ["coven_ping"]), {
    kind: "text",
    text: "PING DONE pong",
  });
});

await test("a result for another call does not count as the ping's", () => {
  const other: ChatMessage[] = [
    ASKED,
    { role: "tool", tool_call_id: "call_2", content: "x" },
  ];
  assert.deepEqual(decidePing(other, ["coven_ping"]), {
    kind: "calls",
    calls: [{ name: "coven_ping", args: {} }],
  });
});
