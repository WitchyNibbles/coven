import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { serverStatus } from "../src/pm/status.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, gmWorld } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

const NOW = 10_000_000;
const MINUTE = 60_000;

interface Rig {
  world: World;
  asks: Map<string, unknown[]>;
  texts: Map<string, string | Error>;
  status: ReturnType<typeof serverStatus>;
}

function rig(extra: readonly SessionLike[] = []): Rig {
  const world = gmWorld([
    GM,
    { id: "pm1", metadata: coven("pm", "acme"), time: { busy: NOW - 5 } },
    ...extra,
  ]);
  const asks = new Map<string, unknown[]>();
  const texts = new Map<string, string | Error>();
  const status = serverStatus({
    session: world,
    permission: {
      list: ({ sessionID }) => Promise.resolve(asks.get(sessionID) ?? []),
    },
    lastText: (sessionID) => {
      const text = texts.get(sessionID) ?? "";
      return text instanceof Error
        ? Promise.reject(text)
        : Promise.resolve(text);
    },
    now: () => NOW,
  });
  return { world, asks, texts, status };
}

await test("active holds the sessions whose turn runs, children included", async () => {
  const { status } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 5, idle: 3 } },
    { id: "kid2", parentID: "pm1", time: { busy: 5, idle: 9 } },
    { id: "rest", time: { busy: 5, idle: 5 } },
  ]);
  assert.deepEqual([...(await status.active())].sort(), ["kid1", "pm1"]);
});

await test("active pages through the whole list", async () => {
  const { world, status } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 5 } },
    { id: "kid2", parentID: "pm1", time: { busy: 5 } },
  ]);
  world.pageSize = 1;
  assert.deepEqual([...(await status.active())].sort(), [
    "kid1",
    "kid2",
    "pm1",
  ]);
  assert.ok(world.pages >= 4);
});

await test("active rejects when the list cannot be read", async () => {
  const { world, status } = rig();
  world.failList = true;
  await assert.rejects(status.active(), /list down/);
});

await test("active rejects on a list that never ends", async () => {
  const { world, status } = rig();
  world.endless = true;
  await assert.rejects(status.active(), /more than \d+ pages/);
});

await test("childBusyMs is how long the longest-busy child has been busy", async () => {
  const { status } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: NOW - 31 * MINUTE } },
    { id: "kid2", parentID: "pm1", time: { busy: NOW - 2 * MINUTE } },
    { id: "kid3", parentID: "kid1", time: { busy: NOW - 40 * MINUTE } },
    { id: "kid4", parentID: "pm1", time: { busy: 1, idle: 2 } },
  ]);
  assert.equal(await status.childBusyMs("pm1"), 40 * MINUTE);
});

await test("childBusyMs is undefined with no busy child", async () => {
  const { status } = rig([
    { id: "kid1", parentID: "pm1", time: { busy: 1, idle: 2 } },
  ]);
  assert.equal(await status.childBusyMs("pm1"), undefined);
});

await test("childBusyMs rejects when the children cannot be read", async () => {
  const { world, status } = rig();
  world.failList = true;
  await assert.rejects(status.childBusyMs("pm1"), /list down/);
});

await test("asks holds the request ids waiting in the pm and below it", async () => {
  const { asks, status } = rig([{ id: "kid1", parentID: "pm1" }]);
  asks.set("pm1", [{ id: "per_1" }]);
  asks.set("kid1", [{ requestID: "per_2" }, { id: "per_3" }, { nothing: 1 }]);
  assert.deepEqual(await status.asks("pm1"), [
    "per_1",
    "per_2",
    "per_3",
    "unknown id",
  ]);
});

await test("asks is empty when nothing waits", async () => {
  const { status } = rig([{ id: "kid1", parentID: "pm1" }]);
  assert.deepEqual(await status.asks("pm1"), []);
});

await test("asks rejects when an ask list or the tree cannot be read", async () => {
  const { world, status } = rig([{ id: "kid1", parentID: "pm1" }]);
  const broken = serverStatus({
    session: world,
    permission: { list: () => Promise.reject(new Error("permission down")) },
    lastText: () => Promise.resolve(""),
    now: () => NOW,
  });
  await assert.rejects(broken.asks("pm1"), /permission down/);
  world.failList = true;
  await assert.rejects(status.asks("pm1"), /list down/);
});

await test("asks rejects on an answer that is not a list", async () => {
  const { world } = rig();
  const odd = serverStatus({
    session: world,
    permission: { list: () => Promise.resolve({} as unknown as unknown[]) },
    lastText: () => Promise.resolve(""),
    now: () => NOW,
  });
  await assert.rejects(odd.asks("pm1"), /not a list/);
});

await test("lastMessage is the last assistant text, or undefined with none", async () => {
  const { texts, status } = rig();
  texts.set("pm1", "first\nlast");
  assert.equal(await status.lastMessage("pm1"), "first\nlast");
  assert.equal(await status.lastMessage("kid9"), undefined);
});

await test("lastMessage rejects when the history cannot be read", async () => {
  const { texts, status } = rig();
  texts.set("pm1", new Error("context down"));
  await assert.rejects(status.lastMessage("pm1"), /context down/);
});
