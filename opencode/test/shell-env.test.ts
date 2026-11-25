import assert from "node:assert/strict";
import { test } from "node:test";

import type { ShellCreateEvent } from "../src/context.ts";
import { scrubShellEnv } from "../src/shell-env.ts";
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

await test("the key variable and the server password are removed", () => {
  const event = shellEvent({ [KEY]: "k", [PASSWORD]: "p", PATH: "/bin" });
  scrubShellEnv(KEY)(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("every other variable is left alone, near-misses included", () => {
  const env = {
    HOME: "/h",
    PATH: "/bin",
    [`${KEY}_2`]: "other",
    [`X_${KEY}`]: "other",
    OPENCODE_SERVER_USERNAME: "opencode",
    [KEY.toLowerCase()]: "lower",
  };
  const event = shellEvent({ ...env, [KEY]: "k", [PASSWORD]: "p" });
  scrubShellEnv(KEY)(event);
  assert.deepEqual(event.env, env);
});

await test("an env without them is left as it was, with no keys added", () => {
  const env = { PATH: "/bin" };
  const event = shellEvent({ ...env });
  scrubShellEnv(KEY)(event);
  assert.deepEqual(event.env, env);
  assert.deepEqual(Object.keys(event.env), ["PATH"]);
});

await test("only the key variable is removed when the password is absent", () => {
  const event = shellEvent({ [KEY]: "k", PATH: "/bin" });
  scrubShellEnv(KEY)(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
});

await test("the rest of the event is not touched", () => {
  const event = shellEvent({ [KEY]: "k" });
  scrubShellEnv(KEY)(event);
  assert.deepEqual(
    { ...event, env: undefined },
    { ...shellEvent({}), env: undefined },
  );
});

await test("the plugin registers one create.before shell hook that scrubs", async () => {
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
  const event = shellEvent({ [KEY]: "k", [PASSWORD]: "p", PATH: "/bin" });
  await p.shellHooks[0]?.callback(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
  cleanup();
});
