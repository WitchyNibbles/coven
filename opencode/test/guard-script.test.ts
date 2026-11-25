import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import type { Payload } from "../src/deny-secrets.ts";
import { HOOK_TIMEOUTS_MS, runHookScript } from "../src/guard.ts";
import { ROLE_ENV } from "../src/pm/role.ts";
import { exits, fakeSpawn } from "./guard-fake.ts";
import type { Behaviour } from "./guard-fake.ts";

const ROOT = "/opt/coven";

const READ: Payload = {
  tool_name: "Read",
  tool_input: { file_path: "a.txt" },
  cwd: "/work",
  hook_event_name: "PreToolUse",
};

/** Runs `body` with `setTimeout` mocked, so a timeout is a tick of the clock and not a wait. */
async function withClock(body: () => Promise<void>): Promise<void> {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    await body();
  } finally {
    mock.timers.reset();
  }
}

await test("the timeouts are 5 s, 5 s, 15 s and 30 s", () => {
  assert.deepEqual(HOOK_TIMEOUTS_MS, {
    denySecrets: 5000,
    serverGuard: 5000,
    pmPermissions: 15_000,
    prodGuard: 30_000,
  });
});

function runScript(
  script: string,
  behaviour: Behaviour,
  extraArgs: readonly string[] = [],
  timeoutMs = 5000,
  env?: NodeJS.ProcessEnv,
) {
  const { spawn, calls } = fakeSpawn(behaviour);
  return {
    calls,
    outcome: runHookScript(
      ROOT,
      script,
      extraArgs,
      READ,
      timeoutMs,
      env,
      spawn,
    ),
  };
}

await test("a hook script runs through fail-closed with its extra args after it", async () => {
  const { calls, outcome } = runScript("opencode_server_guard.py", exits(0), [
    "--profile",
    "/p",
    "--serve",
    "/s",
    "--coven-root",
    ROOT,
  ]);
  await outcome;
  assert.equal(calls[0]?.command, "sh");
  assert.deepEqual(calls[0]?.args, [
    "/opt/coven/hooks/fail-closed.sh",
    "/opt/coven/hooks/opencode_server_guard.py",
    "--profile",
    "/p",
    "--serve",
    "/s",
    "--coven-root",
    ROOT,
  ]);
  assert.equal(calls[0]?.stdin, JSON.stringify(READ));
});

await test("the child env is passed to the spawn; none means inherit", async () => {
  const withEnv = runScript("pm_permissions.py", exits(0), [], 5000, {
    [ROLE_ENV]: "pm",
  });
  await withEnv.outcome;
  assert.deepEqual(withEnv.calls[0]?.env, { [ROLE_ENV]: "pm" });
  const plain = runScript("pm_permissions.py", exits(0));
  await plain.outcome;
  assert.equal(plain.calls[0]?.env, undefined);
});

await test("each script's refusal names that script", async () => {
  await assert.rejects(
    runScript("pm_permissions.py", exits(2, "git push is not allowed\n"))
      .outcome,
    /^Error: git push is not allowed$/,
  );
  await assert.rejects(
    runScript("pm_permissions.py", exits(2)).outcome,
    /^Error: coven pm-permissions: blocked$/,
  );
  await assert.rejects(
    runScript("prod_guard.py", exits(1)).outcome,
    /^Error: coven prod-guard: guard failed \(exit 1\); blocking$/,
  );
  await assert.rejects(
    runScript("opencode_server_guard.py", (child) => {
      child.emit("error", new Error("spawn sh ENOENT"));
    }).outcome,
    /^Error: coven opencode-server-guard: could not start the guard \(spawn sh ENOENT\); blocking$/,
  );
});

await test("each hook gets its own timeout", async () => {
  await withClock(async () => {
    const slow: Behaviour = (child) => {
      setTimeout(() => {
        child.finish(0);
      }, 10_000);
    };
    const server = runScript(
      "opencode_server_guard.py",
      slow,
      [],
      HOOK_TIMEOUTS_MS.serverGuard,
    );
    const refused = assert.rejects(
      server.outcome,
      /^Error: coven opencode-server-guard: guard timed out after 5000 ms; blocking$/,
    );
    const pm = runScript(
      "pm_permissions.py",
      slow,
      [],
      HOOK_TIMEOUTS_MS.pmPermissions,
    );
    await tick();
    mock.timers.tick(10_000);
    await refused;
    await pm.outcome;
    assert.deepEqual(server.calls[0]?.kills, ["SIGKILL"]);
    assert.deepEqual(pm.calls[0]?.kills, []);
  });
});
