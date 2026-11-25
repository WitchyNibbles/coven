import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { resolveRole } from "../src/pm/role.ts";
import { sharedState } from "../src/pm/state.ts";
import { sendTool } from "../src/pm/tools.ts";
import { coven, fakeSession } from "./pm-fake-session.ts";
import type { FakeSession } from "./pm-fake-session.ts";

const SESSIONS: SessionLike[] = [
  { id: "gm1", metadata: coven("gm") },
  { id: "pm1", metadata: coven("pm", "x1") },
  { id: "pm2", metadata: coven("pm", "x2") },
  { id: "kid", parentID: "pm1" },
  { id: "plain" },
  { id: "old", metadata: coven("former-gm") },
];

function world(extra: readonly SessionLike[] = []): FakeSession {
  return fakeSession([...SESSIONS, ...extra]);
}

function send(fake: FakeSession, sessionID: string, input: unknown) {
  const tool = sendTool({ session: fake, resolve: resolveRole });
  return tool.execute(input, {
    sessionID,
    signal: new AbortController().signal,
    progress: () => Promise.resolve(),
  });
}

await test('gm to pm by code with ANSWER queues "[from gm] ANSWER ..."', async () => {
  const fake = world();
  await send(fake, "gm1", { to: "x2", text: "ANSWER x2/1: yes\nmore" });
  assert.deepEqual(fake.prompts, [
    {
      sessionID: "pm2",
      text: "[from gm] ANSWER x2/1: yes\nmore",
      delivery: "queue",
    },
  ]);
});

await test('pm to gm with DONE queues "[from pm-x1] DONE ..."', async () => {
  const fake = world();
  await send(fake, "pm1", { text: "DONE x1/1 merged" });
  assert.deepEqual(fake.prompts, [
    {
      sessionID: "gm1",
      text: "[from pm-x1] DONE x1/1 merged",
      delivery: "queue",
    },
  ]);
});

await test("a pm's `to` is ignored: the message goes to the gm", async () => {
  const fake = world();
  await send(fake, "pm1", { to: "x2", text: "QUESTION x1/1 which one?" });
  assert.equal(fake.prompts[0]?.sessionID, "gm1");
});

await test("every pm prefix goes to the gm and every gm prefix to a pm", async () => {
  for (const word of ["QUESTION", "PROD-STEP", "DRAFT", "DONE", "BLOCKED"]) {
    const fake = world();
    await send(fake, "pm1", { text: `${word} x1/1` });
    assert.equal(fake.prompts.length, 1, word);
  }
  for (const word of ["ANSWER", "APPROVED", "DENIED", "TASK"]) {
    const fake = world();
    await send(fake, "gm1", { to: "x1", text: `${word} x1/1` });
    assert.equal(fake.prompts.length, 1, word);
  }
});

await test("bad prefix is a tool error, nothing sent", async () => {
  const fake = world();
  await assert.rejects(
    send(fake, "gm1", { to: "x1", text: "hello x1/1" }),
    /GM message must start with one of ANSWER, APPROVED, DENIED, TASK/,
  );
  await assert.rejects(
    send(fake, "gm1", { to: "x1", text: "DONE x1/1" }),
    /GM message must start with one of/,
  );
  await assert.rejects(
    send(fake, "pm1", { text: "ANSWER x1/1" }),
    /PM message must start with one of/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("a pm child cannot send", async () => {
  const fake = world();
  await assert.rejects(send(fake, "kid", { text: "DONE x1/1" }), /root/);
  assert.deepEqual(fake.prompts, []);
});

await test("a gm's child cannot send as the gm", async () => {
  const fake = world([{ id: "gmkid", parentID: "gm1" }]);
  await assert.rejects(
    send(fake, "gmkid", { to: "x1", text: "TASK x1/1" }),
    /root/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("unknown code", async () => {
  const fake = world();
  await assert.rejects(
    send(fake, "gm1", { to: "nope", text: "ANSWER nope/1" }),
    /no PM with code "nope"/,
  );
  await assert.rejects(
    send(fake, "gm1", { text: "ANSWER x1/1" }),
    /to is required/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("a pm child's copy of a code is not the pm", async () => {
  const fake = fakeSession([
    { id: "gm1", metadata: coven("gm") },
    { id: "kid", parentID: "pm1", metadata: coven("pm", "x1") },
  ]);
  await assert.rejects(
    send(fake, "gm1", { to: "x1", text: "ANSWER x1/1" }),
    /no PM with code "x1"/,
  );
});

await test('no live session holds role gm → "no GM session; end the turn with the full message"', async () => {
  const fake = fakeSession([{ id: "pm1", metadata: coven("pm", "x1") }]);
  await assert.rejects(
    send(fake, "pm1", { text: "DONE x1/1" }),
    /no GM session; end the turn with the full message/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("send marks the turn as messaged", async () => {
  const fake = world();
  sharedState().messaged.delete("pm1");
  await send(fake, "pm1", { text: "DONE x1/1" });
  assert.equal(sharedState().messaged.has("pm1"), true);
  sharedState().messaged.delete("pm1");
});

await test("a send that fails is not marked and is a tool error", async () => {
  const fake = world();
  sharedState().messaged.delete("pm1");
  fake.failPrompt = true;
  await assert.rejects(send(fake, "pm1", { text: "DONE x1/1" }), /prompt down/);
  assert.equal(sharedState().messaged.has("pm1"), false);
});

await test("a list that throws is a tool error, nothing sent", async () => {
  const fake = world();
  fake.failList = true;
  await assert.rejects(send(fake, "pm1", { text: "DONE x1/1" }), /list down/);
  await assert.rejects(
    send(fake, "gm1", { to: "x1", text: "ANSWER x1/1" }),
    /list down/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("send refused for a session with no role", async () => {
  const fake = world();
  await assert.rejects(send(fake, "plain", { text: "DONE x1/1" }), /no role/);
  await assert.rejects(send(fake, "old", { text: "DONE x1/1" }), /GM or a PM/);
  assert.deepEqual(fake.prompts, []);
});

await test("a caller whose role cannot be read is a pm without a code: refused", async () => {
  const fake = fakeSession([
    { id: "gm1", metadata: coven("gm") },
    { id: "odd", metadata: { coven: "gm" } },
  ]);
  await assert.rejects(
    send(fake, "odd", { text: "DONE x1/1" }),
    /no code in its metadata/,
  );
  assert.deepEqual(fake.prompts, []);
});

await test("bad input is a tool error", async () => {
  const fake = world();
  for (const input of [
    null,
    "DONE",
    {},
    { text: "" },
    { text: 5 },
    { to: 3, text: "DONE x" },
  ]) {
    await assert.rejects(send(fake, "pm1", input), /text|input|to/);
  }
  assert.deepEqual(fake.prompts, []);
});
