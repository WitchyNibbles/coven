import assert from "node:assert/strict";
import { mkdirSync, readFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import type { SessionLike } from "../src/context.ts";
import { LINE_LIMIT, isValidCode } from "../src/pm/code.ts";
import { pmLaunchTool } from "../src/pm/launch.ts";
import { resolveRole } from "../src/pm/role.ts";
import { pmRules } from "../src/pm/rules.ts";
import { ToolError } from "../src/pm/tools.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, SECRET, call, gitRepo, gmWorld, plainDir } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

const PROFILE = "/placeholder/profile";
const STORE = "/placeholder/store";

function launcher(world: World, store: string | null = STORE) {
  return pmLaunchTool({
    session: world,
    resolve: resolveRole,
    options: { store, profileDir: PROFILE, secretRules: SECRET },
  });
}

function launch(world: World, input: unknown, as = "gm1") {
  return call(launcher(world), as, input);
}

await test("launch creates a session with agent project-manager, location = the repo checkout, metadata {role pm, code}, permissions = pmRules, title pm-<code>, then prompts the brief", async () => {
  const repo = gitRepo();
  const world = gmWorld([GM]);
  const content = await launch(world, {
    code: "acme-1",
    repo,
    brief: "Build the thing.\nSecond line.",
  });
  assert.deepEqual(world.created, [
    {
      agent: "project-manager",
      location: { directory: repo },
      title: "pm-acme-1",
      metadata: { coven: { role: "pm", code: "acme-1" } },
      permissions: pmRules({
        store: STORE,
        storeResolved: null,
        tmp: `${PROFILE}/tmp`,
        secretRules: SECRET,
        extraRepos: [],
      }),
    },
  ]);
  assert.deepEqual(world.prompts, [
    { sessionID: "new1", text: "Build the thing.\nSecond line." },
  ]);
  assert.match(content, /pm-acme-1/);
  assert.match(content, /new1/);
});

await test("launch takes a title and no store", async () => {
  const world = gmWorld([GM]);
  await launch(
    world,
    { code: "acme-1", repo: gitRepo(), brief: "go", title: "Acme work" },
    "gm1",
  );
  const bare = launcher(world, null);
  await call(bare, "gm1", { code: "b", repo: gitRepo(), brief: "go" });
  assert.equal(world.created[0]?.title, "Acme work");
  const rules = world.created[1]?.permissions ?? [];
  assert.equal(
    rules.some((each) => each.resource.startsWith(STORE)),
    false,
  );
});

await test("a store behind a symbolic link is denied by both paths", async () => {
  const base = plainDir();
  const real = join(base, "real-store");
  const link = join(base, "link-store");
  mkdirSync(real);
  symlinkSync(real, link);
  const world = gmWorld([GM]);
  await call(launcher(world, link), "gm1", {
    code: "acme-1",
    repo: gitRepo(),
    brief: "go",
  });
  const denied = (world.created[0]?.permissions ?? [])
    .filter((each) => each.action === "edit" && each.effect === "deny")
    .map((each) => each.resource);
  assert.ok(denied.includes(`${link}/*`));
  assert.ok(denied.includes(`${real}/*`));
});

await test("extraRepos become external_directory allows", async () => {
  const world = gmWorld([GM]);
  await launch(world, {
    code: "acme-1",
    repo: gitRepo(),
    brief: "go",
    extraRepos: ["/placeholder/lib-a", "/placeholder/lib-b"],
  });
  const allows = (world.created[0]?.permissions ?? [])
    .filter((each) => each.action === "external_directory")
    .map((each) => each.resource);
  assert.deepEqual(allows, [
    `${PROFILE}/tmp/*`,
    "/placeholder/lib-a/*",
    "/placeholder/lib-b/*",
  ]);
});

await test("launch is refused for a live code, and an archived PM of that code does not count", async () => {
  const live: SessionLike = { id: "pm1", metadata: coven("pm", "acme-1") };
  const world = gmWorld([GM, live]);
  await assert.rejects(
    launch(world, { code: "acme-1", repo: gitRepo(), brief: "go" }),
    /already has a live PM/,
  );
  assert.equal(world.created.length, 0);
  world.archived.add("pm1");
  await launch(world, { code: "acme-1", repo: gitRepo(), brief: "go" });
  assert.equal(world.created.length, 1);
});

await test("launch is refused for a repo that is not a git checkout", async () => {
  const world = gmWorld([GM]);
  for (const repo of [
    plainDir(),
    join(gitRepo(), "missing"),
    "relative/repo",
  ]) {
    await assert.rejects(
      launch(world, { code: "acme-1", repo, brief: "go" }),
      /not a git checkout/,
    );
  }
  const inside = join(gitRepo(), "sub");
  mkdirSync(inside);
  await assert.rejects(
    launch(world, { code: "acme-1", repo: inside, brief: "go" }),
    /not a git checkout/,
  );
  assert.equal(world.created.length, 0);
});

await test("an invalid code is refused", async () => {
  const world = gmWorld([GM]);
  const repo = gitRepo();
  for (const code of [
    "-a",
    "Acme",
    "a;touch x",
    "a b",
    "a\n",
    "a/b",
    "a_b",
    ".a",
  ]) {
    await assert.rejects(
      launch(world, { code, repo, brief: "go" }),
      /project code/,
      JSON.stringify(code),
    );
  }
  await assert.rejects(
    launch(world, { code: "a".repeat(LINE_LIMIT + 1), repo, brief: "go" }),
    /project code/,
  );
  assert.equal(world.created.length, 0);
});

await test("valid codes pass", () => {
  for (const code of [
    "acme-1",
    "a",
    "9x",
    "web-2026",
    "a".repeat(LINE_LIMIT),
  ]) {
    assert.ok(isValidCode(code), code);
  }
});

await test("the code rule matches src/coven/pm/text.py", () => {
  const source = readFileSync(
    new URL("../../src/coven/pm/text.py", import.meta.url),
    "utf8",
  );
  assert.match(source, /^LINE_LIMIT = (\d+)$/m);
  assert.equal(Number(/^LINE_LIMIT = (\d+)$/m.exec(source)?.[1]), LINE_LIMIT);
  assert.match(
    source,
    /^PROJECT_CODE = re\.compile\(r"\[a-z0-9\]\[a-z0-9-\]\*"\)$/m,
  );
});

await test("bad input is a tool error and nothing is created", async () => {
  const world = gmWorld([GM]);
  const repo = gitRepo();
  for (const input of [
    null,
    { code: "", repo, brief: "go" },
    { repo, brief: "go" },
    { code: "a", brief: "go" },
    { code: "a", repo },
    { code: "a", repo, brief: "  " },
    { code: "a", repo, brief: "go", extraRepos: "x" },
    { code: "a", repo, brief: "go", extraRepos: [1] },
    { code: "a", repo, brief: "go", extraRepos: ["relative"] },
    { code: "a", repo, brief: "go", title: 3 },
  ]) {
    await assert.rejects(
      launch(world, input),
      ToolError,
      JSON.stringify(input),
    );
  }
  assert.equal(world.created.length, 0);
});

await test("a failed create is a tool error and no prompt is sent", async () => {
  const world = gmWorld([GM]);
  world.create = () => Promise.reject(new Error("create down"));
  await assert.rejects(
    launch(world, { code: "a", repo: gitRepo(), brief: "go" }),
    (error: unknown) =>
      error instanceof ToolError && /create down/.test(error.message),
  );
  assert.equal(world.prompts.length, 0);
});

await test("a brief that cannot be delivered names the new session", async () => {
  const world = gmWorld([GM]);
  world.failPrompt = true;
  await assert.rejects(
    launch(world, { code: "a", repo: gitRepo(), brief: "go" }),
    (error: unknown) =>
      error instanceof ToolError &&
      /new1/.test(error.message) &&
      /prompt down/.test(error.message),
  );
});

await test("launch is refused when the live PM listing cannot be read", async () => {
  const world = gmWorld([GM]);
  world.failList = true;
  await assert.rejects(
    launch(world, { code: "a", repo: gitRepo(), brief: "go" }),
    (error: unknown) =>
      error instanceof ToolError && /list down/.test(error.message),
  );
  assert.equal(world.created.length, 0);
});

await test("a repo given through a symbolic link starts the PM in the resolved top folder", async () => {
  const repo = gitRepo();
  const link = join(plainDir(), "repo-link");
  symlinkSync(repo, link);
  const world = gmWorld([GM]);
  const content = await launch(world, {
    code: "acme-1",
    repo: link,
    brief: "go",
  });
  assert.equal(world.created[0]?.location?.directory, repo);
  assert.match(content, new RegExp(`in ${repo}$`));
});
