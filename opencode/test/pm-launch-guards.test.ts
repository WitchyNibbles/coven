import assert from "node:assert/strict";
import { test } from "node:test";

import { isValidCode } from "../src/pm/code.ts";
import { pmLaunchTool } from "../src/pm/launch.ts";
import { resolveRole } from "../src/pm/role.ts";
import { ToolError } from "../src/pm/tools.ts";
import { coven } from "./pm-fake-session.ts";
import { GM, SECRET, call, gitRepo, gmWorld, plainDir } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

const PROFILE = "/placeholder/profile";

function launch(world: World, input: unknown, as = "gm1") {
  const tool = pmLaunchTool({
    session: world,
    resolve: resolveRole,
    options: { store: null, profileDir: PROFILE, secretRules: SECRET },
  });
  return call(tool, as, input);
}

await test("two launches of one code at once create exactly one session", async () => {
  const world = gmWorld([GM]);
  const results = await Promise.allSettled([
    launch(world, { code: "acme-1", repo: gitRepo(), brief: "one" }),
    launch(world, { code: "acme-1", repo: gitRepo(), brief: "two" }),
  ]);
  assert.equal(world.created.length, 1);
  assert.deepEqual(results.map((each) => each.status).sort(), [
    "fulfilled",
    "rejected",
  ]);
  const refused = results.find((each) => each.status === "rejected");
  assert.match(
    String((refused as PromiseRejectedResult).reason),
    /already launching acme-1/,
  );
  await launch(world, { code: "other", repo: gitRepo(), brief: "go" });
  assert.equal(world.created.length, 2);
});

await test("a failed launch frees its code for another try", async () => {
  const world = gmWorld([GM]);
  world.failList = true;
  await assert.rejects(
    launch(world, { code: "a", repo: gitRepo(), brief: "go" }),
  );
  world.failList = false;
  await launch(world, { code: "a", repo: gitRepo(), brief: "go" });
  assert.equal(world.created.length, 1);
});

await test("an extra repo with a trailing slash gives a single slash before the star", async () => {
  const world = gmWorld([GM]);
  await launch(world, {
    code: "acme-1",
    repo: gitRepo(),
    brief: "go",
    extraRepos: ["/placeholder/lib-a/", "/placeholder/lib-b//"],
  });
  const allows = (world.created[0]?.permissions ?? [])
    .filter((each) => each.action === "external_directory")
    .map((each) => each.resource);
  assert.deepEqual(allows, [
    `${PROFILE}/tmp/*`,
    "/placeholder/lib-a/*",
    "/placeholder/lib-b/*",
  ]);
  await assert.rejects(
    launch(world, {
      code: "b",
      repo: gitRepo(),
      brief: "go",
      extraRepos: ["/"],
    }),
    ToolError,
  );
});

await test("the empty code is not a project code", () => {
  assert.equal(isValidCode(""), false);
});

await test("a git that cannot run is a git failure, not a missing checkout", async () => {
  const world = gmWorld([GM]);
  const repo = gitRepo();
  const empty = plainDir();
  const path = process.env["PATH"];
  process.env["PATH"] = empty;
  try {
    await assert.rejects(
      launch(world, { code: "a", repo, brief: "go" }),
      (error: unknown) =>
        error instanceof ToolError &&
        /^git failed: /.test(error.message) &&
        !/not a git checkout/.test(error.message),
    );
  } finally {
    if (path === undefined) {
      delete process.env["PATH"];
    } else {
      process.env["PATH"] = path;
    }
  }
  assert.equal(world.created.length, 0);
});

await test("a pm calling launch with a malformed input gets the GM-only refusal", async () => {
  const world = gmWorld([GM, { id: "pm1", metadata: coven("pm", "x") }]);
  for (const input of [
    null,
    { code: 3 },
    { code: "x", repo: "r", brief: "" },
  ]) {
    await assert.rejects(launch(world, input, "pm1"), /only the GM can/);
  }
});

await test("a pm launch running with a GM launch of the same code does not make the GM's fail", async () => {
  const world = gmWorld([GM, { id: "pm1", metadata: coven("pm", "x") }]);
  const input = { code: "acme-1", repo: gitRepo(), brief: "go" };
  const [refused, made] = await Promise.allSettled([
    launch(world, input, "pm1"),
    launch(world, input),
  ]);
  assert.equal(refused.status, "rejected");
  assert.equal(made.status, "fulfilled");
  assert.equal(world.created.length, 1);
});
