import assert from "node:assert/strict";
import { test } from "node:test";

import { ROLE_ENV } from "../src/pm/role.ts";
import { exits } from "./guard-fake.ts";
import {
  COVEN,
  SCRIPTS_FOR_GM,
  SCRIPTS_FOR_PM,
  SERVER,
  booted,
  cleanups,
  perScript,
  scripts,
  toolEvent,
} from "./plugin-guards-fixture.ts";
import {
  DIRECTORY,
  LEAD,
  PM,
  PM_KID,
  PROFILE_OPTIONS,
} from "./plugin-fixture.ts";

await test("order: deny-secrets, server guard, pm-permissions, prod guard for a pm session", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM));
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM);
  assert.deepEqual(
    p.spawned.map((call) => call.args.slice(2)),
    [
      [],
      [
        "--profile",
        PROFILE_OPTIONS.profileDir,
        "--serve",
        PROFILE_OPTIONS.serveDir,
        "--coven-root",
        COVEN.covenRoot,
      ],
      ["--pre-tool-use"],
      ["--no-approvals"],
    ],
  );
});

await test("a child of a pm gets the pm guards", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM_KID));
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM);
});

await test("gm session runs only deny-secrets and the server guard", async () => {
  const { p, run } = await booted();
  await run(toolEvent(LEAD));
  assert.deepEqual(scripts(p), SCRIPTS_FOR_GM);
});

await test("a former gm and a session with no role run only the first two", async () => {
  const { p, run } = await booted();
  p.sessions.set("ses_old", {
    id: "ses_old",
    metadata: { coven: { role: "former-gm" } },
  });
  p.sessions.set("ses_plain", { id: "ses_plain" });
  await run(toolEvent("ses_old"));
  await run(toolEvent("ses_plain"));
  assert.deepEqual(scripts(p), [...SCRIPTS_FOR_GM, ...SCRIPTS_FOR_GM]);
});

await test("a failed role lookup runs the pm guards", async () => {
  const { p, run } = await booted();
  p.sessions.set("ses_down", new Error("down"));
  await run(toolEvent("ses_down"));
  await run(toolEvent("ses_unknown"));
  assert.deepEqual(scripts(p), [...SCRIPTS_FOR_PM, ...SCRIPTS_FOR_PM]);
});

await test("a null metadata is no role of its own and the chain goes on", async () => {
  const { p, run } = await booted();
  p.sessions.set("ses_null", {
    id: "ses_null",
    parentID: PM,
    metadata: null as unknown as Record<string, unknown>,
  });
  await run(toolEvent("ses_null"));
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM);
});

await test("pm-permissions and prod guard children get ROLE_ENV=pm", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM));
  assert.deepEqual(
    p.spawned.map((call) => call.env),
    [undefined, undefined, { [ROLE_ENV]: "pm" }, { [ROLE_ENV]: "pm" }],
  );
});

await test("the payload cwd is the call's workdir", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM, { command: "ls", workdir: "/w/tree" }));
  await run(toolEvent(PM, { command: "ls", workdir: "sub/dir" }));
  await run(toolEvent(PM, { command: "ls" }));
  await run(toolEvent(PM, { path: "a.txt" }, "read"));
  const cwds = p.spawned.map(
    (call) => (JSON.parse(call.stdin) as { cwd: string }).cwd,
  );
  const expected = [
    "/w/tree",
    "/work/session/sub/dir",
    DIRECTORY,
    DIRECTORY,
  ].flatMap((cwd) => [cwd, cwd, cwd, cwd]);
  assert.deepEqual(cwds, expected);
});

await test("every hook of a call gets the same payload", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM));
  assert.equal(new Set(p.spawned.map((call) => call.stdin)).size, 1);
});

await test("a failing pm-permissions refuses with its stderr and prod guard never starts", async () => {
  const { p, run } = await booted({
    guard: perScript({
      "pm_permissions.py": exits(2, "pm-permissions: push refused\n"),
    }),
  });
  await assert.rejects(
    run(toolEvent(PM)),
    /^Error: pm-permissions: push refused$/,
  );
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM.slice(0, 3));
});

await test("a refusal by the server guard stops the later hooks", async () => {
  const { p, run } = await booted({
    guard: perScript({ [SERVER]: exits(2, "opencode: refused") }),
  });
  await assert.rejects(run(toolEvent(PM)), /^Error: opencode: refused$/);
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM.slice(0, 2));
});

await test("a refusal by deny-secrets stops the later hooks", async () => {
  const { p, run } = await booted({
    guard: perScript({ "deny_secrets.py": exits(2, "deny: no") }),
  });
  await assert.rejects(run(toolEvent(PM)), /^Error: deny: no$/);
  assert.deepEqual(scripts(p), SCRIPTS_FOR_PM.slice(0, 1));
});

await test("a pm guard that fails with any other exit refuses", async () => {
  const { run } = await booted({
    guard: perScript({ "prod_guard.py": exits(1) }),
  });
  await assert.rejects(
    run(toolEvent(PM)),
    /^Error: coven prod-guard: guard failed \(exit 1\); blocking$/,
  );
});

await test("a tool no guard covers starts no child and no lookup", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM, {}, "coven_ping"));
  assert.deepEqual(p.spawned, []);
  assert.deepEqual(p.lookups, []);
});

await test("a guard refusal on a shell call records nothing for its shell", async () => {
  const { p, run } = await booted({
    guard: perScript({ "deny_secrets.py": exits(2, "deny: no") }),
  });
  await assert.rejects(
    run(toolEvent(PM, { command: "echo refused" })),
    /^Error: deny: no$/,
  );
  const shell = p.shellHooks[0];
  assert.ok(shell !== undefined);
  const event = {
    command: "echo refused",
    cwd: DIRECTORY,
    env: {} as Record<string, string>,
  };
  await shell.callback(event);
  assert.equal(event.env[ROLE_ENV], undefined);
});

await test("a shell call looks its role up once, for the guards and for its shell", async () => {
  const { p, run } = await booted();
  await run(toolEvent(PM, { command: "echo once" }));
  assert.deepEqual(p.lookups, [PM]);
  const shell = p.shellHooks[0];
  assert.ok(shell !== undefined);
  const event = {
    command: "echo once",
    cwd: DIRECTORY,
    shell: "/usr/bin/bash",
    env: {} as Record<string, string>,
  };
  await shell.callback(event);
  assert.equal(event.env[ROLE_ENV], "pm");
});

await test("a failed role lookup on a shell call still gives the shell a pm", async () => {
  const { p, run } = await booted();
  p.sessions.set("ses_down", new Error("down"));
  await run(toolEvent("ses_down", { command: "echo down" }));
  assert.deepEqual(p.lookups, ["ses_down"]);
  const shell = p.shellHooks[0];
  assert.ok(shell !== undefined);
  const event = {
    command: "echo down",
    cwd: DIRECTORY,
    shell: "/usr/bin/bash",
    env: {} as Record<string, string>,
  };
  await shell.callback(event);
  assert.equal(event.env[ROLE_ENV], "pm");
});

await test("the plugins the guard tests started stop", () => {
  for (const stop of cleanups) {
    stop();
  }
});
