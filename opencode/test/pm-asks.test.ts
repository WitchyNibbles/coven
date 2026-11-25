import assert from "node:assert/strict";
import { test } from "node:test";

import type { EvaluateEvent, SessionLike } from "../src/context.ts";
import { askToDeny } from "../src/pm/asks.ts";
import type { RoleResolver } from "../src/pm/asks.ts";
import { resolveRole } from "../src/pm/role.ts";
import type { RoleInfo } from "../src/pm/role.ts";

const MESSAGE =
  "a PM may not wait on a prompt: send it to the GM as a QUESTION or PROD-STEP";

function event(
  effect: EvaluateEvent["effect"],
  message?: string,
): EvaluateEvent {
  return {
    sessionID: "ses_1",
    action: "bash",
    resources: ["*"],
    effect,
    ...(message === undefined ? {} : { message }),
  };
}

const asks =
  (role: RoleInfo | undefined): RoleResolver =>
  () =>
    Promise.resolve(role);

await test("ask becomes deny for a pm child", async () => {
  const get = (id: string): Promise<SessionLike | undefined> =>
    Promise.resolve(
      id === "ses_1"
        ? { id, parentID: "ses_pm" }
        : { id, metadata: { coven: { role: "pm", code: "proj" } } },
    );
  const seen = event("ask");
  await askToDeny((id) => resolveRole(id, get))(seen);
  assert.equal(seen.effect, "deny");
  assert.equal(seen.message, MESSAGE);
});

await test("ask becomes deny for a pm session itself", async () => {
  const seen = event("ask");
  await askToDeny(asks({ role: "pm", code: "proj" }))(seen);
  assert.equal(seen.effect, "deny");
  assert.equal(seen.message, MESSAGE);
});

await test("ask becomes deny when the lookup fails", async () => {
  const failing = (): Promise<SessionLike | undefined> =>
    Promise.reject(new Error("down"));
  const seen = event("ask");
  await askToDeny((id) => resolveRole(id, failing))(seen);
  assert.equal(seen.effect, "deny");
  assert.equal(seen.message, MESSAGE);
});

await test("ask becomes deny when the resolver itself throws", async () => {
  const seen = event("ask");
  await askToDeny(() => Promise.reject(new Error("boom")))(seen);
  assert.equal(seen.effect, "deny");
  assert.equal(seen.message, MESSAGE);
});

await test("ask stays ask for the gm", async () => {
  const seen = event("ask");
  await askToDeny(asks({ role: "gm" }))(seen);
  assert.equal(seen.effect, "ask");
  assert.equal(seen.message, undefined);
});

await test("ask stays ask for a former gm and for a session with no role", async () => {
  for (const role of [{ role: "former-gm" } as const, undefined]) {
    const seen = event("ask");
    await askToDeny(asks(role))(seen);
    assert.equal(seen.effect, "ask");
  }
});

await test("allow stays allow", async () => {
  const seen = event("allow");
  await askToDeny(asks({ role: "pm" }))(seen);
  assert.equal(seen.effect, "allow");
  assert.equal(seen.message, undefined);
});

await test("deny stays deny with its own reason", async () => {
  const seen = event("deny", "secrets file");
  await askToDeny(asks({ role: "pm" }))(seen);
  assert.equal(seen.effect, "deny");
  assert.equal(seen.message, "secrets file");
});

await test("an allow or deny does not look the role up", async () => {
  let looked = 0;
  const resolve: RoleResolver = () => {
    looked += 1;
    return Promise.resolve({ role: "pm" });
  };
  await askToDeny(resolve)(event("allow"));
  await askToDeny(resolve)(event("deny"));
  assert.equal(looked, 0);
});
