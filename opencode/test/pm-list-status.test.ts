import assert from "node:assert/strict";
import { test } from "node:test";

import { pmListTool } from "../src/pm/manage.ts";
import { resolveRole } from "../src/pm/role.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, call, gmWorld, quietStatus } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

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
