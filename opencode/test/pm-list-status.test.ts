import assert from "node:assert/strict";
import { test } from "node:test";

import { pmListTool } from "../src/pm/manage.ts";
import { resolveRole } from "../src/pm/role.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, call, gmWorld, quietStatus } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";
import { notFound } from "./plugin-events.ts";

function list(world: World): Promise<string> {
  const tool = pmListTool({
    session: world,
    resolve: resolveRole,
    status: quietStatus(),
  });
  return call(tool, "gm1", {});
}

await test("list says missing only for a not-found get, and names any other get failure", async () => {
  const world = gmWorld([
    GM,
    { id: "pm1", metadata: coven("pm", "one") },
    { id: "pm2", metadata: coven("pm", "two") },
    { id: "pm3", metadata: coven("pm", "three") },
  ]);
  const get = world.get.bind(world);
  world.get = (input) => {
    if (input.sessionID === "pm1") {
      return Promise.reject(Object.assign(new Error("gone"), { status: 404 }));
    }
    if (input.sessionID === "pm2") {
      return Promise.reject(new Error("boom"));
    }
    return get(input);
  };
  assert.deepEqual((await list(world)).split("\n"), [
    "pm-one  pm1  missing",
    "pm-two  pm2  status unavailable: boom",
    "pm-three  pm3  idle",
  ]);
});

await test("list takes the outcome from get over the listed one", async () => {
  const world = gmWorld([
    GM,
    { id: "pm1", metadata: coven("pm", "acme-1"), outcome: "succeeded" },
  ]);
  const get = world.get.bind(world);
  world.get = async (input) => ({ ...(await get(input)), outcome: "failed" });
  assert.match(await list(world), /last failed/);
  const bare = gmWorld([GM, { id: "pm1", metadata: coven("pm", "acme-1") }]);
  const base = bare.list.bind(bare);
  bare.list = async (input) => {
    const page = await base(input);
    return {
      ...page,
      data: page.data.map((each) => ({ ...each, outcome: "interrupted" })),
    };
  };
  assert.match(await list(bare), /last interrupted/);
});

await test("only a real not-found is missing: a status decides alone, else the error name or exact text", async () => {
  const failures: [string, Error][] = [
    ["404", Object.assign(new Error("gone"), { status: 404 })],
    ["404 code", Object.assign(new Error("gone"), { statusCode: 404 })],
    ["name", Object.assign(new Error("x"), { name: "NotFoundError" })],
    ["text", new Error("Session not found")],
    ["500", Object.assign(new Error("Model not found"), { status: 500 })],
    [
      "500 name",
      Object.assign(new Error("x"), { name: "NotFoundError", status: 500 }),
    ],
    ["dns", new Error("getaddrinfo ENOTFOUND example.test")],
    ["other text", new Error("model not found upstream")],
    // what 2.3.2 throws for a deleted session: name and tag, no status, no message
    ["real", notFound("real")],
    [
      "http tag",
      Object.assign(new Error("Session not found: x"), {
        _tag: "SessionNotFoundError",
        sessionID: "x",
      }),
    ],
    [
      "other tag",
      Object.assign(new Error(""), { _tag: "Permission.NotFoundError" }),
    ],
    [
      "other name",
      Object.assign(new Error(""), { name: "Model.NotFoundError" }),
    ],
    ["not a name", Object.assign(new Error(""), { _tag: "Session.NotFound" })],
    // a message's not-found that carries the session it was looked up in is not a gone session
    [
      "message tag",
      Object.assign(new Error(""), {
        _tag: "Message.NotFoundError",
        sessionID: "x",
      }),
    ],
    [
      "message name",
      Object.assign(new Error(""), {
        name: "Message.NotFoundError",
        sessionID: "x",
      }),
    ],
    ["real 500", Object.assign(notFound("real 500"), { status: 500 })],
  ];
  const world = gmWorld([
    GM,
    ...failures.map(([id]) => ({
      id,
      metadata: coven("pm", id.replace(" ", "-")),
    })),
  ]);
  const get = world.get.bind(world);
  world.get = (input) => {
    const found = failures.find(([id]) => id === input.sessionID);
    return found === undefined ? get(input) : Promise.reject(found[1]);
  };
  const lines = (await list(world)).split("\n");
  const verdict = (id: string) =>
    lines.find((line) => line.includes(`  ${id}  `))?.split(`  ${id}  `)[1];
  for (const id of ["404", "404 code", "name", "text", "real", "http tag"]) {
    assert.equal(verdict(id), "missing", id);
  }
  for (const id of [
    "500",
    "500 name",
    "dns",
    "other text",
    "other tag",
    "other name",
    "not a name",
    "message tag",
    "message name",
    "real 500",
  ]) {
    assert.match(verdict(id) ?? "", /^status unavailable: /, id);
  }
});

await test("list calls a PM busy when its busy stamp is smaller than its list index", async () => {
  const idle = ["a", "b", "c", "d", "e"].map((id) => ({
    id: `idle-${id}`,
    time: { busy: 1, idle: 2 },
  }));
  const world = gmWorld([
    GM,
    ...idle,
    { id: "pm1", metadata: coven("pm", "one"), time: { busy: 3, idle: 1 } },
  ]);
  assert.equal(await list(world), "pm-one  pm1  busy");
});

await test("list reads the busy set and the PMs across pages, and fails on a listing that never ends", async () => {
  const world = gmWorld([
    GM,
    { id: "pm1", metadata: coven("pm", "one"), time: { busy: 5 } },
    { id: "pm2", metadata: coven("pm", "two") },
  ]);
  world.pageSize = 1;
  assert.deepEqual((await list(world)).split("\n"), [
    "pm-one  pm1  busy",
    "pm-two  pm2  idle",
  ]);
  world.endless = true;
  await assert.rejects(list(world), /more than \d+ pages/);
});
