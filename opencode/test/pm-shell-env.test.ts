import assert from "node:assert/strict";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";

import type { Role } from "../src/pm/role.ts";
import { ShellQueue, pmIsolation, scrubEnv } from "../src/pm/shell-env.ts";
import { sharedState } from "../src/pm/state.ts";

const KEY = "GATEWAY_KEY";
const PASSWORD = "OPENCODE_SERVER_PASSWORD";

function entry(role: Role | undefined, command = "ls", cwd = "/work") {
  return { role, command, cwd };
}

/** A fresh evaluation of `state.ts`, as 2.3.2 makes one per location. */
async function stateCopy(copy: number): Promise<{
  sharedState: typeof sharedState;
}> {
  const url = new URL("../src/pm/state.ts", import.meta.url);
  url.searchParams.set("copy", String(copy));
  return (await import(url.href)) as { sharedState: typeof sharedState };
}

await test("two module copies see one sharedState", async () => {
  const first = await stateCopy(1);
  const second = await stateCopy(2);
  assert.notEqual(first.sharedState, second.sharedState);
  assert.equal(first.sharedState(), second.sharedState());
  assert.equal(sharedState(), first.sharedState());
  assert.ok(sharedState().queue instanceof ShellQueue);
  assert.ok(sharedState().messaged instanceof Set);
  assert.ok(sharedState().stopped instanceof Set);
});

await test("take returns the role recorded for the command and cwd", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1001), "pm");
});

await test("an entry is taken once", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  queue.take("ls", "/work", 1001);
  assert.equal(queue.take("ls", "/work", 1002), undefined);
});

await test("no entry sets no role", () => {
  const queue = new ShellQueue();
  assert.equal(queue.take("ls", "/work", 1000), undefined);
});

await test("an entry for another command or cwd is left alone", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm", "ls", "/work"), 1000);
  assert.equal(queue.take("pwd", "/work", 1001), undefined);
  assert.equal(queue.take("ls", "/other", 1001), undefined);
  assert.equal(queue.take("ls", "/work", 1001), "pm");
});

await test("an entry whose role is undefined takes no role", () => {
  const queue = new ShellQueue();
  queue.record(entry(undefined), 1000);
  assert.equal(queue.take("ls", "/work", 1001), undefined);
});

await test("test_same_key_different_roles_gets_pm", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work"), 1000);
  queue.record(entry("pm", "ls", "/work"), 1001);
  assert.equal(queue.take("ls", "/work", 1002), "pm");
});

await test("a role and no role under one key also get pm", () => {
  const queue = new ShellQueue();
  queue.record(entry(undefined, "ls", "/work"), 1000);
  queue.record(entry("gm", "ls", "/work"), 1001);
  assert.equal(queue.take("ls", "/work", 1002), "pm");
});

await test("oldest entry first", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work"), 1000);
  queue.record(entry("gm", "ls", "/work"), 1001);
  queue.record(entry("pm", "ls", "/other"), 1002);
  assert.equal(queue.take("ls", "/work", 1003), "gm");
  assert.equal(queue.take("ls", "/work", 1004), "gm");
  assert.equal(queue.take("ls", "/work", 1005), undefined);
  assert.equal(queue.take("ls", "/other", 1006), "pm");
});

await test("expired entry ignored", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1000 + 30_000), undefined);
});

await test("an entry just inside the expiry still pairs", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm"), 1000);
  assert.equal(queue.take("ls", "/work", 1000 + 29_999), "pm");
});

await test("an expired entry does not turn a live one into pm", () => {
  const queue = new ShellQueue();
  queue.record(entry("gm", "ls", "/work"), 1000);
  queue.record(entry("pm", "ls", "/work"), 40_000);
  assert.equal(queue.take("ls", "/work", 40_001), "pm");
  queue.record(entry("gm", "ls", "/work"), 50_000);
  assert.equal(queue.take("ls", "/work", 50_001), "gm");
});

await test("a relative workdir pairs by its raw string", () => {
  const queue = new ShellQueue();
  queue.record(entry("pm", "make", "sub"), 1000);
  assert.equal(queue.take("make", "/work/sub", 1001), undefined);
  assert.equal(queue.take("make", "sub", 1001), "pm");
});

await test("scrub removes password and key, keeps PATH and HOME", () => {
  const env: Record<string, string | undefined> = {
    [PASSWORD]: "p",
    OPENCODE_PASSWORD: "p",
    [KEY]: "k",
    OPENCODE_API_TOKEN: "t",
    OPENCODE_SIGNING_KEY: "s",
    OPENCODE_ADMIN_PASSWORD: "a",
    OPENCODE_TOKEN: "t",
    OPENCODE_KEY: "k",
    OPENCODE_X_TOKEN: "x",
    PATH: "/bin",
    HOME: "/h",
  };
  scrubEnv(env, KEY);
  assert.deepEqual(env, { PATH: "/bin", HOME: "/h" });
});

await test("scrub removes the wrapper's two test seams", () => {
  const env: Record<string, string | undefined> = {
    COVEN_ISOLATE_SYS: "/fake/sys",
    COVEN_ISOLATE_PATH: "/fake/bin",
    PATH: "/bin",
  };
  scrubEnv(env, KEY);
  assert.deepEqual(env, { PATH: "/bin" });
});

const PROFILE = {
  serveDir: "/tmp/coven-test/profile/state/serve",
  dataDir: "/tmp/coven-test/profile/data",
};

await test("the folders to hide are the serve folder and the data folder options, pinned", () => {
  const found = pmIsolation({ covenRoot: "/c", ...PROFILE });
  assert.equal(
    found.hide,
    "/tmp/coven-test/profile/state/serve:/tmp/coven-test/profile/data",
  );
});

await test("the data folder is the option as given, not derived from anything", () => {
  const found = pmIsolation({
    covenRoot: "/c",
    serveDir: "/s/serve",
    dataDir: "/elsewhere/data",
  });
  assert.equal(found.hide, "/s/serve:/elsewhere/data");
});

await test("the wrapper is hooks/pm-shell-isolate.sh under the coven root", () => {
  assert.equal(
    pmIsolation({ covenRoot: "/c", ...PROFILE }).wrapper,
    "/c/hooks/pm-shell-isolate.sh",
  );
});

await test("the wrapper is found when it is an executable file and not otherwise", () => {
  const root = mkdtempSync(join(tmpdir(), "coven-wrapper-"));
  try {
    const options = { covenRoot: root, ...PROFILE };
    assert.equal(pmIsolation(options).found, false);
    const wrapper = pmIsolation(options).wrapper;
    mkdirSync(dirname(wrapper), { recursive: true });
    writeFileSync(wrapper, "#!/bin/sh\n", { mode: 0o644 });
    assert.equal(pmIsolation(options).found, false);
    chmodSync(wrapper, 0o755);
    assert.equal(pmIsolation(options).found, true);
    rmSync(wrapper);
    mkdirSync(wrapper);
    chmodSync(wrapper, 0o755);
    assert.equal(pmIsolation(options).found, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

await test("the coven checkout's own wrapper is found", () => {
  const root = resolve(import.meta.dirname, "../..");
  assert.equal(pmIsolation({ covenRoot: root, ...PROFILE }).found, true);
});

await test("scrub leaves near misses alone", () => {
  const env: Record<string, string | undefined> = {
    OPENCODE_SERVER_USERNAME: "opencode",
    OPENCODE_PORT: "1",
    OTHER_TOKEN: "t",
    OTHER_KEY: "k",
    OTHER_PASSWORD: "p",
    OPENCODE_TOKEN_FILE: "f",
    opencode_api_token: "lower",
    [`${KEY}_2`]: "x",
  };
  const before = { ...env };
  scrubEnv(env, KEY);
  assert.deepEqual(env, before);
});
