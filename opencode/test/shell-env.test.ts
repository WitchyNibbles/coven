import assert from "node:assert/strict";
import { test } from "node:test";

import type { ShellCreateEvent } from "../src/context.ts";
import { PROFILE_OPTIONS, bootCoven } from "./plugin-fixture.ts";

const KEY = "GATEWAY_KEY";
const PASSWORD = "OPENCODE_SERVER_PASSWORD";

/** The event as 2.3.2 builds it: the fields the plugin ignores come with `env`. */
function shellEvent(env: Record<string, string>) {
  return {
    command: "echo hi",
    cwd: "/work",
    timeout: 1000,
    shell: "/bin/sh",
    env,
  } satisfies ShellCreateEvent & Record<string, unknown>;
}

/** Runs the plugin's own `create.before` hook on `env`; returns the event afterwards. */
async function throughPlugin(env: Record<string, string>) {
  const p = bootCoven({
    options: {
      covenRoot: "/c",
      keyEnv: KEY,
      ...PROFILE_OPTIONS,
      toolChoiceAuto: [],
    },
  });
  const cleanup = await p.outcome;
  assert.deepEqual(
    p.shellHooks.map((hook) => hook.name),
    ["create.before"],
  );
  const event = shellEvent(env);
  await p.shellHooks[0]?.callback(event);
  cleanup();
  return event;
}

await test("the key variable and the server password are removed", async () => {
  const event = await throughPlugin({
    [KEY]: "k",
    [PASSWORD]: "p",
    PATH: "/bin",
  });
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("every other variable is left alone, near-misses included", async () => {
  const env = {
    HOME: "/h",
    PATH: "/bin",
    [`${KEY}_2`]: "other",
    [`X_${KEY}`]: "other",
    OPENCODE_SERVER_USERNAME: "opencode",
    [KEY.toLowerCase()]: "lower",
  };
  const event = await throughPlugin({ ...env, [KEY]: "k", [PASSWORD]: "p" });
  assert.deepEqual(event.env, env);
});

await test("an env without them is left as it was, with no keys added", async () => {
  const event = await throughPlugin({ PATH: "/bin" });
  assert.deepEqual(Object.keys(event.env), ["PATH"]);
});

await test("only the key variable is removed when the password is absent", async () => {
  const event = await throughPlugin({ [KEY]: "k", PATH: "/bin" });
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("the rest of the event is not touched", async () => {
  const event = await throughPlugin({ [KEY]: "k" });
  assert.deepEqual(
    { ...event, env: undefined },
    { ...shellEvent({}), env: undefined },
  );
});
