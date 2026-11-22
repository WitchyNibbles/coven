import assert from "node:assert/strict";
import { test } from "node:test";

import type { HttpRequestEvent } from "../src/context.ts";
import { addToolChoice, toolChoiceHook } from "../src/tool-choice.ts";

const TOOLS = [{ type: "function", function: { name: "t" } }];

function bodyWith(extra: Record<string, unknown>): string {
  return JSON.stringify({ model: "m", stream: true, messages: [], ...extra });
}

function eventFor(request: Request, sessionID = "s1"): HttpRequestEvent {
  return {
    sessionID,
    agent: "build",
    model: { id: "m", providerID: "litellm" },
    kind: "primary",
    request,
  };
}

function post(body: string | null): Request {
  return new Request("http://gateway.test/v1/chat/completions", {
    method: "POST",
    headers: { authorization: "Bearer k", "content-type": "application/json" },
    body,
  });
}

await test("addToolChoice adds auto to a body with tools, streaming or not", () => {
  for (const stream of [true, false]) {
    const input = { model: "m", stream, tools: TOOLS, messages: [] };
    const result = addToolChoice(JSON.stringify(input));
    assert.equal(result.kind, "changed");
    if (result.kind === "changed") {
      assert.deepEqual(JSON.parse(result.body), {
        ...input,
        tool_choice: "auto",
      });
    }
  }
});

await test("addToolChoice leaves bodies without usable tools or with a tool_choice", () => {
  const unchanged = [
    bodyWith({ tools: [] }),
    bodyWith({}),
    bodyWith({ tools: "x" }),
    bodyWith({ tools: TOOLS, tool_choice: "none" }),
    bodyWith({ tools: TOOLS, tool_choice: null }),
  ];
  for (const body of unchanged) {
    assert.deepEqual(addToolChoice(body), { kind: "unchanged" }, body);
  }
  assert.deepEqual(addToolChoice("[]"), { kind: "unchanged" });
  assert.deepEqual(addToolChoice('"text"'), { kind: "unchanged" });
});

await test("addToolChoice reports a body that is not JSON", () => {
  assert.deepEqual(addToolChoice("not json"), { kind: "unparsable" });
});

await test("the hook swaps in a new request that carries tool_choice auto", async () => {
  const original = post(bodyWith({ tools: TOOLS }));
  const event = eventFor(original);
  await toolChoiceHook(() => assert.fail("must not log"))(event);
  assert.notEqual(event.request, original);
  const sent = JSON.parse(await event.request.text()) as Record<
    string,
    unknown
  >;
  assert.equal(sent.tool_choice, "auto");
  assert.equal(event.request.method, "POST");
  assert.equal(event.request.url, original.url);
  assert.equal(event.request.headers.get("authorization"), "Bearer k");
  assert.equal(event.request.headers.get("content-type"), "application/json");
  assert.equal(event.request.signal.aborted, false);
});

await test("the hook drops a stale content-length and keeps the abort signal", async () => {
  const controller = new AbortController();
  const original = new Request("http://gateway.test/v1/chat/completions", {
    method: "POST",
    headers: { "content-length": "999", "content-type": "application/json" },
    body: bodyWith({ tools: TOOLS }),
    signal: controller.signal,
  });
  const event = eventFor(original);
  await toolChoiceHook(() => undefined)(event);
  assert.equal(event.request.headers.get("content-length"), null);
  controller.abort();
  assert.equal(event.request.signal.aborted, true);
});

await test("the hook keeps the same request when there are no tools", async () => {
  const original = post(bodyWith({}));
  const event = eventFor(original);
  await toolChoiceHook(() => assert.fail("must not log"))(event);
  assert.equal(event.request, original);
  assert.equal(await original.text(), bodyWith({}));
});

await test("the hook keeps the same request for a non-JSON body and logs once per session", async () => {
  const lines: string[] = [];
  const hook = toolChoiceHook((line) => lines.push(line));
  const first = eventFor(post("not json"), "s1");
  const second = eventFor(post("not json"), "s1");
  const third = eventFor(post("not json"), "s2");
  const originals = [first.request, second.request, third.request];
  await hook(first);
  await hook(second);
  await hook(third);
  assert.deepEqual([first.request, second.request, third.request], originals);
  assert.deepEqual(lines, [
    "coven: tool_choice: request body is not JSON; sent unchanged (session s1)",
    "coven: tool_choice: request body is not JSON; sent unchanged (session s2)",
  ]);
});

await test("the hook keeps the same request for a GET with no body", async () => {
  const original = new Request("http://gateway.test/v1/models");
  const event = eventFor(original);
  await toolChoiceHook(() => assert.fail("must not log"))(event);
  assert.equal(event.request, original);
});
