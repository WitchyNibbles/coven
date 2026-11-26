import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import type {
  ExecuteBeforeEvent,
  SessionLike,
  ShellCreateEvent,
} from "../src/context.ts";
import { resolvePlaced } from "../src/pm/role.ts";
import { ShellQueue, shellEnvHooks } from "../src/pm/shell-env.ts";
import type { PmIsolation } from "../src/pm/shell-env.ts";
import { ISOLATION } from "./pm-isolation-fixture.ts";

const SESSION_ENV = "COVEN_PM_SESSION";
const PIDS_ENV = "COVEN_PM_PIDS";
const roots: string[] = [];

// not `after`: a top-level hook may run between two awaited async tests
process.on("exit", () => {
  for (const root of roots) {
    rmSync(root, { recursive: true, force: true });
  }
});

/** A pid folder of its own, so a test's folders are its own. */
function isolationIn(): PmIsolation {
  const root = mkdtempSync(join(tmpdir(), "coven-pids-test-"));
  roots.push(root);
  return { ...ISOLATION, pidsDir: join(root, "isolate", "pids") };
}

const pm = (id: string, parentID?: string): SessionLike => ({
  id,
  location: { directory: "/w" },
  ...(parentID === undefined ? {} : { parentID }),
  metadata: parentID === undefined ? { coven: { role: "pm", code: "p" } } : {},
});
const gm: SessionLike = {
  id: "ses_gm",
  location: { directory: "/w" },
  metadata: { coven: { role: "gm" } },
};

function rig(sessions: Record<string, SessionLike>, isolation: PmIsolation) {
  const hooks = shellEnvHooks({
    keyEnv: "GATEWAY_KEY",
    directory: "/loc",
    isolation,
    queue: new ShellQueue(),
    taken: new WeakSet(),
    now: () => 1_000_000,
  });
  const get = (id: string) => Promise.resolve(sessions[id]);
  return {
    hooks,
    async call(sessionID: string, command = "ls"): Promise<void> {
      const event: ExecuteBeforeEvent = {
        tool: "shell",
        sessionID,
        agent: "build",
        messageID: "m",
        id: "c",
        input: { command },
      };
      const placed = resolvePlaced(sessionID, get);
      await hooks.executeBefore(
        event,
        async () => (await placed).info,
        async () => (await placed).holder,
      );
    },
  };
}

function shell(env: Record<string, string>, command = "ls") {
  return {
    command,
    cwd: "/loc",
    timeout: 1000,
    shell: "/bin/sh",
    env,
  } satisfies ShellCreateEvent & Record<string, unknown>;
}

const mode = (path: string): number => statSync(path).mode & 0o777;

await test("a PM's shell gets its PM session and a pid folder made for it (0700)", async () => {
  const isolation = isolationIn();
  const w = rig({ ses_pm: pm("ses_pm") }, isolation);
  await w.call("ses_pm");
  const event = shell({});
  w.hooks.createBefore(event);
  const folder = join(isolation.pidsDir, "ses_pm");
  assert.equal(event.env[SESSION_ENV], "ses_pm");
  assert.equal(event.env[PIDS_ENV], folder);
  assert.equal(mode(folder), 0o700);
  assert.equal(mode(isolation.pidsDir), 0o700);
});

await test("a shell of a PM's child belongs to the PM tree's root session", async () => {
  const isolation = isolationIn();
  const w = rig(
    { ses_pm: pm("ses_pm"), ses_kid: pm("ses_kid", "ses_pm") },
    isolation,
  );
  await w.call("ses_kid");
  const event = shell({});
  w.hooks.createBefore(event);
  assert.equal(event.env[SESSION_ENV], "ses_pm");
  assert.equal(event.env[PIDS_ENV], join(isolation.pidsDir, "ses_pm"));
});

await test("a folder that already exists is reused, and a second PM gets its own", async () => {
  const isolation = isolationIn();
  const w = rig({ ses_a: pm("ses_a"), ses_b: pm("ses_b") }, isolation);
  for (const id of ["ses_a", "ses_a", "ses_b"]) {
    await w.call(id);
    const event = shell({});
    w.hooks.createBefore(event);
    assert.equal(event.env[SESSION_ENV], id);
  }
  assert.equal(existsSync(join(isolation.pidsDir, "ses_a")), true);
  assert.equal(existsSync(join(isolation.pidsDir, "ses_b")), true);
});

await test("a folder that cannot be made refuses the shell and leaves the event alone", async () => {
  const isolation = isolationIn();
  // the folder for the records is a file: nothing can be made under it
  const root = join(isolation.pidsDir, "..");
  rmSync(root, { recursive: true, force: true });
  writeFileSync(root, "in the way");
  const w = rig({ ses_pm: pm("ses_pm") }, isolation);
  await w.call("ses_pm");
  const event = shell({ PATH: "/bin" });
  assert.throws(
    () => w.hooks.createBefore(event),
    /^Error: coven: PM shells run isolated and isolation is not available here: cannot make the pid folder .*; ask the user$/,
  );
  assert.equal(event.shell, "/bin/sh");
  assert.deepEqual(event.env, { PATH: "/bin" });
  rmSync(root, { force: true });
});

await test("a PM session whose id is not a plain name is refused", async () => {
  const isolation = isolationIn();
  for (const id of ["../x", "a/b", ".hidden", "a b", ""]) {
    const w = rig({ [id]: pm(id) }, isolation);
    await w.call(id);
    const event = shell({});
    assert.throws(
      () => w.hooks.createBefore(event),
      /isolation is not available here: the PM session id .* is not a plain name; ask the user/,
      id,
    );
    assert.equal(event.shell, "/bin/sh");
  }
});

await test("a PM whose session cannot be placed is refused", async () => {
  const isolation = isolationIn();
  const w = rig({}, isolation);
  await w.call("ses_unknown");
  const event = shell({});
  assert.throws(
    () => w.hooks.createBefore(event),
    /the PM session of this shell could not be placed/,
  );
});

await test("values a caller set for the two names are stripped: from a GM's shell and from a PM's, which gets its own", async () => {
  const isolation = isolationIn();
  const w = rig({ ses_pm: pm("ses_pm"), ses_gm: gm }, isolation);
  await w.call("ses_gm", "pwd");
  const ours = { [SESSION_ENV]: "ses_other", [PIDS_ENV]: "/tmp/elsewhere" };
  const gmEvent = shell({ ...ours }, "pwd");
  w.hooks.createBefore(gmEvent);
  assert.deepEqual(gmEvent.env, {});
  await w.call("ses_pm");
  const pmEvent = shell({ ...ours });
  w.hooks.createBefore(pmEvent);
  assert.equal(pmEvent.env[SESSION_ENV], "ses_pm");
  assert.equal(pmEvent.env[PIDS_ENV], join(isolation.pidsDir, "ses_pm"));
});

await test("a shell with no queue entry gets neither name and makes no folder", () => {
  const isolation = isolationIn();
  const w = rig({}, isolation);
  const event = shell({ PATH: "/bin" });
  w.hooks.createBefore(event);
  assert.deepEqual(event.env, { PATH: "/bin" });
  assert.equal(existsSync(isolation.pidsDir), false);
});

await test("a GM and a PM running one command in one folder: the shell the PM's role reaches belongs to the PM", async () => {
  const isolation = isolationIn();
  const w = rig({ ses_pm: pm("ses_pm"), ses_gm: gm }, isolation);
  await w.call("ses_gm");
  await w.call("ses_pm");
  for (let each = 0; each < 2; each += 1) {
    const event = shell({});
    w.hooks.createBefore(event);
    assert.equal(event.env[SESSION_ENV], "ses_pm");
  }
});

await test("a PM's shell that the wrapper refuses for another reason makes no folder", async () => {
  const isolation = { ...isolationIn(), found: false, problem: "gone" };
  const w = rig({ ses_pm: pm("ses_pm") }, isolation);
  await w.call("ses_pm");
  assert.throws(() => w.hooks.createBefore(shell({})), /gone/);
  assert.equal(existsSync(isolation.pidsDir), false);
});
