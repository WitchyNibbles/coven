import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { resolvePlaced, resolveRole } from "../src/pm/role.ts";

function session(id: string, extra: Partial<SessionLike> = {}): SessionLike {
  return { id, location: { directory: "/work" }, ...extra };
}

const coven = (role: string, code?: string): Record<string, unknown> => ({
  coven: { role, ...(code === undefined ? {} : { code }) },
});

function lookup(sessions: Record<string, SessionLike | Error | undefined>) {
  return (id: string): Promise<SessionLike | undefined> => {
    const found = sessions[id];
    return found instanceof Error
      ? Promise.reject(found)
      : Promise.resolve(found);
  };
}

await test("the holder is the session whose own metadata holds the role", async () => {
  const get = lookup({ a: session("a", { metadata: coven("pm", "proj") }) });
  assert.deepEqual(await resolvePlaced("a", get), {
    info: { role: "pm", code: "proj" },
    holder: "a",
  });
});

await test("a child's holder is the ancestor that holds the role, not the child", async () => {
  const get = lookup({
    c: session("c", { parentID: "b" }),
    b: session("b", { parentID: "a" }),
    a: session("a", { metadata: coven("pm", "proj") }),
  });
  const placed = await resolvePlaced("c", get);
  assert.equal(placed.holder, "a");
  assert.deepEqual(placed.info, await resolveRole("c", get));
});

await test("a session with no role has no holder; a lookup that fails closed has none either", async () => {
  assert.deepEqual(await resolvePlaced("p", lookup({ p: session("p") })), {
    info: undefined,
    holder: undefined,
  });
  assert.deepEqual(await resolvePlaced("x", lookup({ x: new Error("down") })), {
    info: { role: "pm" },
    holder: undefined,
  });
  assert.deepEqual(await resolvePlaced("gone", lookup({})), {
    info: { role: "pm" },
    holder: undefined,
  });
});
