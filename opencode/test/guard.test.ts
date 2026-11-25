import assert from "node:assert/strict";
import { basename } from "node:path";
import { mock, test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import type { Payload } from "../src/deny-secrets.ts";
import { guardsHook, runHookScript } from "../src/guard.ts";
import type { SpawnFn } from "../src/guard.ts";
import { exits, fakeSpawn, never } from "./guard-fake.ts";
import type { Behaviour } from "./guard-fake.ts";

const ROOT = "/opt/coven";
const REPO = fileURLToPath(new URL("../..", import.meta.url));

/** deny-secrets on one payload, the way `guardsHook` runs it first. */
function denySecrets(
  covenRoot: string,
  checked: Payload,
  timeoutMs: number,
  spawnFn?: SpawnFn,
): Promise<void> {
  return runHookScript(
    covenRoot,
    "deny_secrets.py",
    [],
    checked,
    timeoutMs,
    undefined,
    spawnFn,
  );
}

function payload(tool_name: string, file: string): Payload {
  return {
    tool_name,
    tool_input: { file_path: file },
    cwd: REPO,
    hook_event_name: "PreToolUse",
  };
}

const READ = payload("Read", "a.txt");

function run(behaviour: Behaviour, timeoutMs = 5000) {
  const { spawn, calls } = fakeSpawn(behaviour);
  return { calls, outcome: denySecrets(ROOT, READ, timeoutMs, spawn) };
}

await test("exit 0 allows", async () => {
  await run(exits(0)).outcome;
});

await test("the child gets the two script paths as an argv array and the payload on stdin", async () => {
  const { calls, outcome } = run(exits(0));
  await outcome;
  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.command, "sh");
  assert.deepEqual(calls[0]?.args, [
    "/opt/coven/hooks/fail-closed.sh",
    "/opt/coven/hooks/deny_secrets.py",
  ]);
  assert.equal(calls[0]?.stdin, JSON.stringify(READ));
});

await test("exit 2 throws the trimmed stderr", async () => {
  await assert.rejects(
    run(exits(2, "coven deny-secrets: access to '.x' is blocked\n")).outcome,
    /^Error: coven deny-secrets: access to '\.x' is blocked$/,
  );
});

await test("exit 2 with no stderr throws a plain blocked message", async () => {
  await assert.rejects(
    run(exits(2)).outcome,
    /^Error: coven deny-secrets: blocked$/,
  );
  await assert.rejects(
    run(exits(2, "  \n")).outcome,
    /^Error: coven deny-secrets: blocked$/,
  );
});

await test("stderr arriving in pieces is joined", async () => {
  await assert.rejects(
    run((child) => {
      child.stderr.emit("data", "coven deny-");
      child.finish(2, "secrets: no");
    }).outcome,
    /^Error: coven deny-secrets: no$/,
  );
});

await test("any other exit blocks and names the code", async () => {
  for (const code of [1, 3, 86, 127]) {
    await assert.rejects(
      run(exits(code, "noise")).outcome,
      new RegExp(
        `^Error: coven deny-secrets: guard failed \\(exit ${code}\\); blocking$`,
      ),
    );
  }
});

await test("a child killed by a signal blocks", async () => {
  await assert.rejects(
    run((child) => {
      child.finish(null, "", "SIGSEGV");
    }).outcome,
    /^Error: coven deny-secrets: guard failed \(signal SIGSEGV\); blocking$/,
  );
});

/** Runs `body` with `setTimeout` mocked, so a timeout is a tick of the clock and not a wait. */
async function withClock(body: () => Promise<void>): Promise<void> {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    await body();
  } finally {
    mock.timers.reset();
  }
}

await test("a timeout kills the child with SIGKILL and blocks", async () => {
  await withClock(async () => {
    const { calls, outcome } = run(never, 20);
    const refused = assert.rejects(
      outcome,
      /^Error: coven deny-secrets: guard timed out after 20 ms; blocking$/,
    );
    await tick();
    mock.timers.tick(19);
    assert.deepEqual(calls[0]?.kills, []);
    mock.timers.tick(1);
    await refused;
    assert.deepEqual(calls[0]?.kills, ["SIGKILL"]);
  });
});

await test("a close after the timeout changes nothing", async () => {
  await withClock(async () => {
    const { calls, outcome } = run((child) => {
      setTimeout(() => {
        child.finish(0);
      }, 60);
    }, 20);
    const refused = assert.rejects(outcome, /timed out after 20 ms/);
    await tick();
    mock.timers.tick(20);
    await refused;
    mock.timers.tick(60);
    assert.deepEqual(calls[0]?.kills, ["SIGKILL"]);
  });
});

await test("a finished guard does not leave the timer or a kill behind", async () => {
  await withClock(async () => {
    const { calls, outcome } = run(exits(0), 20);
    await outcome;
    mock.timers.tick(50);
    assert.deepEqual(calls[0]?.kills, []);
  });
});

await test("a spawn error blocks with its message", async () => {
  await assert.rejects(
    run((child) => {
      child.emit("error", new Error("spawn sh ENOENT"));
    }).outcome,
    /^Error: coven deny-secrets: could not start the guard \(spawn sh ENOENT\); blocking$/,
  );
});

await test("a spawn that throws blocks the same way", async () => {
  await assert.rejects(
    denySecrets(ROOT, READ, 5000, () => {
      throw new Error("no process slots");
    }),
    /^Error: coven deny-secrets: could not start the guard \(no process slots\); blocking$/,
  );
  await assert.rejects(
    denySecrets(ROOT, READ, 5000, () => {
      const failure: unknown = "plain";
      throw failure;
    }),
    /could not start the guard \(plain\); blocking$/,
  );
});

await test("a broken stdin pipe is left to the exit code", async () => {
  await assert.rejects(
    run((child) => {
      child.stdin.emit("error", new Error("EPIPE"));
      child.finish(2, "coven deny-secrets: blocked early");
    }).outcome,
    /blocked early/,
  );
});

await test("messages are cut to 2000 characters", async () => {
  await assert.rejects(run(exits(2, "x".repeat(5000))).outcome, (error) => {
    assert.ok(error instanceof Error);
    assert.equal(error.message.length, 2000);
    return true;
  });
  await assert.rejects(
    run((child) => {
      child.emit("error", new Error("y".repeat(5000)));
    }).outcome,
    (error) => {
      assert.ok(error instanceof Error);
      assert.equal(error.message.length, 2000);
      return true;
    },
  );
});

await test("the real guard refuses an env file and allows a readme", async () => {
  await assert.rejects(
    denySecrets(REPO, payload("Read", ".env"), 30_000),
    /^Error: coven deny-secrets: /,
  );
  await denySecrets(REPO, payload("Read", "README.md"), 30_000);
});

await test("the real guard refuses a shell whose workdir is the ssh directory", async () => {
  const shell: Payload = {
    tool_name: "Bash",
    tool_input: { command: "cat id_rsa", workdir: `${REPO}/.ssh` },
    cwd: REPO,
    hook_event_name: "PreToolUse",
  };
  await assert.rejects(
    denySecrets(REPO, shell, 30_000),
    /^Error: coven deny-secrets: /,
  );
  const plain: Payload = {
    ...shell,
    tool_input: { command: "cat id_rsa", workdir: `${REPO}/docs` },
  };
  await denySecrets(REPO, plain, 30_000);
});

const PM_GUARDS = [
  "deny_secrets.py",
  "opencode_server_guard.py",
  "pm_permissions.py",
  "prod_guard.py",
];

/** The hook children `guardsHook` starts for one shell call, by script, with `resolve` given. */
async function guardsFor(resolve: () => never | Promise<never>) {
  const { spawn, calls } = fakeSpawn(exits(0));
  const hook = guardsHook(
    {
      covenRoot: ROOT,
      guardTimeoutMs: 5000,
      profileDir: "/p",
      serveDir: "/p/state/serve",
    },
    { directory: "/w", resolve, spawn },
  );
  await hook({
    tool: "shell",
    sessionID: "ses_x",
    agent: "build",
    messageID: "msg_1",
    id: "call_1",
    input: { command: "ls" },
  });
  return calls.map((call) => basename(call.args[1] ?? ""));
}

await test("a role resolver that throws runs the pm guards", async () => {
  const scripts = await guardsFor(() => {
    throw new Error("lookup crashed");
  });
  assert.deepEqual(scripts, PM_GUARDS);
});

await test("a role resolver that rejects runs the pm guards", async () => {
  const scripts = await guardsFor(() =>
    Promise.reject(new Error("server down")),
  );
  assert.deepEqual(scripts, PM_GUARDS);
});
