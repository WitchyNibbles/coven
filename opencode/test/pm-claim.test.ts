import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { MAX_PAGES } from "../src/pm/busy.ts";
import { findGm } from "../src/pm/find.ts";
import { resolveRole } from "../src/pm/role.ts";
import { gmClaimTool, sendTool } from "../src/pm/tools.ts";
import { coven, fakeSession } from "./pm-fake-session.ts";
import type { FakeSession } from "./pm-fake-session.ts";

function root(id: string, metadata?: Record<string, unknown>): SessionLike {
  return metadata === undefined ? { id } : { id, metadata };
}

async function claim(fake: FakeSession, sessionID: string): Promise<string> {
  const tool = gmClaimTool({ session: fake, resolve: resolveRole });
  const signal = new AbortController().signal;
  const out = await tool.execute(
    {},
    { sessionID, signal, progress: () => Promise.resolve() },
  );
  return out.content;
}

function sendAs(fake: FakeSession, sessionID: string, input: unknown) {
  const tool = sendTool({ session: fake, resolve: resolveRole });
  const signal = new AbortController().signal;
  return tool.execute(input, {
    sessionID,
    signal,
    progress: () => Promise.resolve(),
  });
}

await test("first claim sets metadata role gm", async () => {
  const fake = fakeSession([root("s1")]);
  await claim(fake, "s1");
  assert.deepEqual(fake.sessions.get("s1")?.metadata, coven("gm"));
  assert.equal((await findGm(fake))?.id, "s1");
});

await test("a claim keeps the other metadata of the session", async () => {
  const fake = fakeSession([
    root("s1", { note: 1, ...coven("former-gm", "c") }),
  ]);
  await claim(fake, "s1");
  assert.deepEqual(fake.sessions.get("s1")?.metadata, {
    note: 1,
    coven: { role: "gm", code: "c" },
  });
});

await test("a second claim takes over: the old GM becomes former-gm first, then the new one gm; a PM's send now reaches the new GM", async () => {
  const fake = fakeSession([
    root("s1", coven("gm")),
    root("s2"),
    root("pm1", coven("pm", "x1")),
  ]);
  await claim(fake, "s2");
  assert.deepEqual(
    fake.calls.filter((call) => call.startsWith("update")),
    ["update:s1:former-gm", "update:s2:gm"],
  );
  assert.equal((await findGm(fake))?.id, "s2");
  await sendAs(fake, "pm1", { text: "DONE x1/1 finished" });
  assert.equal(fake.prompts[0]?.sessionID, "s2");
});

await test("a claim by the current GM writes nothing", async () => {
  const fake = fakeSession([root("s1", coven("gm"))]);
  await claim(fake, "s1");
  assert.deepEqual(
    fake.calls.filter((call) => call.startsWith("update")),
    [],
  );
});

await test("a claim demotes every other GM, then sets the caller", async () => {
  const fake = fakeSession([
    root("s1", coven("gm")),
    root("s2", coven("gm")),
    root("s3"),
  ]);
  await claim(fake, "s3");
  assert.deepEqual(
    fake.calls.filter((call) => call.startsWith("update")),
    ["update:s1:former-gm", "update:s2:former-gm", "update:s3:gm"],
  );
  assert.equal((await findGm(fake))?.id, "s3");
});

await test("a claim by a GM demotes any other GM and leaves itself alone", async () => {
  const fake = fakeSession([root("s1", coven("gm")), root("s2", coven("gm"))]);
  const content = await claim(fake, "s2");
  assert.deepEqual(
    fake.calls.filter((call) => call.startsWith("update")),
    ["update:s1:former-gm"],
  );
  assert.match(content, /already the GM/);
  assert.deepEqual(fake.sessions.get("s2")?.metadata, coven("gm"));
});

await test("a claim whose second write fails leaves no GM and is a tool error", async () => {
  const fake = fakeSession([
    root("s1", coven("gm")),
    root("s2"),
    root("pm1", coven("pm", "x1")),
  ]);
  fake.failUpdate = 2;
  await assert.rejects(claim(fake, "s2"), /update down/);
  assert.equal(await findGm(fake), undefined);
  await assert.rejects(
    sendAs(fake, "pm1", { text: "DONE x1/1" }),
    /no GM session; end the turn with the full message/,
  );
});

await test("a claim whose first write fails changes nothing", async () => {
  const fake = fakeSession([root("s1", coven("gm")), root("s2")]);
  fake.failUpdate = 1;
  await assert.rejects(claim(fake, "s2"), /update down/);
  assert.equal((await findGm(fake))?.id, "s1");
  assert.equal(fake.sessions.get("s2")?.metadata, undefined);
});

await test("claim refused for a session with parentID", async () => {
  const fake = fakeSession([root("s1"), { id: "s3", parentID: "s1" }]);
  await assert.rejects(claim(fake, "s3"), /root session/);
  assert.equal(fake.calls.filter((c) => c.startsWith("update")).length, 0);
});

await test("claim refused for a pm", async () => {
  const fake = fakeSession([root("pm1", coven("pm", "x1"))]);
  await assert.rejects(claim(fake, "pm1"), /a PM cannot claim/);
  assert.equal(fake.calls.filter((c) => c.startsWith("update")).length, 0);
});

await test("claim refused when the caller's role cannot be read", async () => {
  const fake = fakeSession([root("s1", { coven: "gm" })]);
  await assert.rejects(claim(fake, "s1"), /a PM cannot claim/);
});

await test("claim refused when the caller cannot be looked up", async () => {
  const fake = fakeSession([]);
  await assert.rejects(claim(fake, "missing"), /no session missing/);
});

await test("a claim is a tool error when listing sessions throws, with no write", async () => {
  const fake = fakeSession([root("s1")]);
  fake.failList = true;
  await assert.rejects(claim(fake, "s1"), /list down/);
  assert.equal(fake.calls.filter((c) => c.startsWith("update")).length, 0);
});

await test("findGm ignores a GM's child that copied role gm", async () => {
  const fake = fakeSession([
    { id: "kid", parentID: "s1", metadata: coven("gm") },
    root("s1", coven("gm")),
  ]);
  assert.equal((await findGm(fake))?.id, "s1");
  const only = fakeSession([
    { id: "kid", parentID: "s1", metadata: coven("gm") },
    { id: "kid2", parentID: null, metadata: coven("pm", "x") },
  ]);
  assert.equal(await findGm(only), undefined);
});

await test("findGm follows cursor.next to the last page", async () => {
  const fake = fakeSession([root("a"), root("b"), root("c", coven("gm"))]);
  fake.pageSize = 1;
  assert.equal((await findGm(fake))?.id, "c");
  assert.equal(fake.pages, 3);
});

await test("findGm fails closed on a list that throws or never ends", async () => {
  const down = fakeSession([root("s1", coven("gm"))]);
  down.failList = true;
  await assert.rejects(findGm(down), /list down/);
  const endless = fakeSession([root("s1")]);
  endless.endless = true;
  await assert.rejects(findGm(endless), /more than 50 pages/);
  assert.equal(endless.pages, MAX_PAGES);
});

await test("two claims at once leave exactly one GM", async () => {
  const fake = fakeSession([root("s1"), root("s2")]);
  const results = await Promise.allSettled([
    claim(fake, "s1"),
    claim(fake, "s2"),
  ]);
  assert.deepEqual(results.map((each) => each.status).sort(), [
    "fulfilled",
    "rejected",
  ]);
  const refused = results.find((each) => each.status === "rejected");
  assert.match(
    String((refused as PromiseRejectedResult).reason),
    /a claim is already running/,
  );
  const gms = [...fake.sessions.values()].filter(
    (each) =>
      (each.metadata?.["coven"] as { role?: string } | undefined)?.role ===
      "gm",
  );
  assert.equal(gms.length, 1);
});

await test("a failed claim frees the lock", async () => {
  const fake = fakeSession([root("s1")]);
  fake.failList = true;
  await assert.rejects(claim(fake, "s1"), /list down/);
  fake.failList = false;
  await claim(fake, "s1");
  assert.equal((await findGm(fake))?.id, "s1");
});

await test("a pm claim running with a real claim does not make the real one fail", async () => {
  const fake = fakeSession([root("pm1", coven("pm", "x")), root("s2")]);
  const [refused, made] = await Promise.allSettled([
    claim(fake, "pm1"),
    claim(fake, "s2"),
  ]);
  assert.equal(refused.status, "rejected");
  assert.equal(made.status, "fulfilled");
  assert.equal((await findGm(fake))?.id, "s2");
});
