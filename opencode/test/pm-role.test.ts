import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { ROLE_ENV, isRoot, resolveRole } from "../src/pm/role.ts";

type Sessions = Record<string, SessionLike | Error | undefined>;

function session(id: string, extra: Partial<SessionLike> = {}): SessionLike {
  return { id, location: { directory: "/work" }, ...extra };
}

function coven(role: string, code?: string): Record<string, unknown> {
  return { coven: { role, ...(code === undefined ? {} : { code }) } };
}

/** A `get` over a fixed table: an `Error` entry throws, a missing one returns nothing. */
function lookup(sessions: Sessions) {
  const asked: string[] = [];
  const get = (id: string): Promise<SessionLike | undefined> => {
    asked.push(id);
    const found = sessions[id];
    return found instanceof Error
      ? Promise.reject(found)
      : Promise.resolve(found);
  };
  return { get, asked };
}

await test("role from own metadata", async () => {
  const { get, asked } = lookup({
    a: session("a", { metadata: coven("pm", "proj") }),
  });
  assert.deepEqual(await resolveRole("a", get), { role: "pm", code: "proj" });
  assert.deepEqual(asked, ["a"]);
});

await test("a role without a code has no code key", async () => {
  const { get } = lookup({ a: session("a", { metadata: coven("gm") }) });
  assert.deepEqual(await resolveRole("a", get), { role: "gm" });
});

await test("role from parent chain", async () => {
  const { get } = lookup({
    c: session("c", { parentID: "b" }),
    b: session("b", { parentID: "a" }),
    a: session("a", { metadata: coven("pm", "proj") }),
  });
  assert.deepEqual(await resolveRole("c", get), { role: "pm", code: "proj" });
});

await test("the nearest role wins over a parent's", async () => {
  const { get } = lookup({
    b: session("b", { parentID: "a", metadata: coven("former-gm") }),
    a: session("a", { metadata: coven("gm") }),
  });
  assert.deepEqual(await resolveRole("b", get), { role: "former-gm" });
});

await test("no role", async () => {
  const { get } = lookup({
    b: session("b", { parentID: "a", metadata: { other: 1 } }),
    a: session("a"),
  });
  assert.equal(await resolveRole("b", get), undefined);
});

await test("cycle → pm", async () => {
  const { get } = lookup({
    a: session("a", { parentID: "b" }),
    b: session("b", { parentID: "a" }),
  });
  assert.deepEqual(await resolveRole("a", get), { role: "pm" });
});

await test("a session that is its own parent → pm", async () => {
  const { get } = lookup({ a: session("a", { parentID: "a" }) });
  assert.deepEqual(await resolveRole("a", get), { role: "pm" });
});

await test("get throws → pm", async () => {
  const { get } = lookup({ a: new Error("boom") });
  assert.deepEqual(await resolveRole("a", get), { role: "pm" });
});

await test("get throws mid-chain → pm", async () => {
  const { get } = lookup({
    b: session("b", { parentID: "a" }),
    a: new Error("boom"),
  });
  assert.deepEqual(await resolveRole("b", get), { role: "pm" });
});

await test("get returns nothing mid-chain → pm", async () => {
  const { get } = lookup({ b: session("b", { parentID: "a" }) });
  assert.deepEqual(await resolveRole("b", get), { role: "pm" });
});

await test("get returns nothing for the session itself → pm", async () => {
  const { get } = lookup({});
  assert.deepEqual(await resolveRole("a", get), { role: "pm" });
});

await test("more than 32 hops → pm, and the walk stops", async () => {
  const sessions: Sessions = {};
  for (let i = 0; i < 40; i += 1) {
    sessions[`s${i}`] = session(`s${i}`, { parentID: `s${i + 1}` });
  }
  sessions["s40"] = session("s40", { metadata: coven("gm") });
  const { get, asked } = lookup(sessions);
  assert.deepEqual(await resolveRole("s0", get), { role: "pm" });
  assert.ok(asked.length <= 33, `asked ${asked.length} sessions`);
});

await test("a chain of exactly 32 sessions still resolves", async () => {
  const sessions: Sessions = {};
  for (let i = 0; i < 31; i += 1) {
    sessions[`s${i}`] = session(`s${i}`, { parentID: `s${i + 1}` });
  }
  sessions["s31"] = session("s31", { metadata: coven("gm") });
  const { get } = lookup(sessions);
  assert.deepEqual(await resolveRole("s0", get), { role: "gm" });
});

await test("a coven entry that is not a known role → pm", async () => {
  for (const metadata of [
    coven("root"),
    { coven: "gm" },
    { coven: null },
    { coven: {} },
  ]) {
    const { get } = lookup({ a: session("a", { metadata }) });
    assert.deepEqual(await resolveRole("a", get), { role: "pm" });
  }
});

await test("null or non-object metadata is no role of its own", async () => {
  for (const metadata of [null, "gm", 5, ["coven"]]) {
    const { get } = lookup({
      b: session("b", {
        parentID: "a",
        metadata: metadata as unknown as Record<string, unknown>,
      }),
      a: session("a", { metadata: coven("pm", "proj") }),
    });
    assert.deepEqual(await resolveRole("b", get), {
      role: "pm",
      code: "proj",
    });
  }
  const { get } = lookup({
    a: session("a", {
      metadata: null as unknown as Record<string, unknown>,
    }),
  });
  assert.equal(await resolveRole("a", get), undefined);
});

await test("a null parentID ends the chain like a missing one", async () => {
  const { get, asked } = lookup({ a: session("a", { parentID: null }) });
  assert.equal(await resolveRole("a", get), undefined);
  assert.deepEqual(asked, ["a"]);
});

await test("isRoot is true only without a parent", () => {
  assert.equal(isRoot(session("a")), true);
  assert.equal(isRoot(session("a", { parentID: null })), true);
  assert.equal(isRoot(session("a", { parentID: "b" })), false);
});

await test("ROLE_ENV matches the Python value", () => {
  const source = readFileSync(
    new URL("../../src/coven/pm/profile.py", import.meta.url),
    "utf8",
  );
  const found = /"env":\s*\{\s*"([A-Z_]+)":/.exec(source);
  assert.notEqual(found, null);
  assert.equal(ROLE_ENV, found?.[1]);
});
