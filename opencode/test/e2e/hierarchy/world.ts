/**
 * The hierarchy suite's world: the runtime world's scratch HOME and profile, a memory store whose
 * `systems/guard.json` holds one made-up target, two git repos for the PMs, a probe plugin, and
 * `coven opencode serve` started in it. Everything lives under the harness's temp folder.
 */
import { cp, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { coven } from "../../harness/coven.ts";
import { startFakeModel } from "../../harness/fake-model.ts";
import { exec } from "../../harness/process.ts";
import type { Finished } from "../../harness/process.ts";
import { addPlugin, makeWorld, pointProviderAt } from "../runtime/profile.ts";
import type { RuntimeWorld } from "../runtime/profile.ts";
import { apiFor } from "./api.ts";
import type { Api } from "./api.ts";
import { decideHierarchy } from "./script.ts";

const PROBE = fileURLToPath(new URL("./probe", import.meta.url));
const COMMAND_TIMEOUT_MS = 90_000;
const GIT_TIMEOUT_MS = 30_000;

/** The one made-up host of the scratch store's guard.json; it never resolves. */
export const TARGET_HOST = "db.example.invalid";

const GUARD = {
  targets: [
    {
      system: "scratch-db",
      kind: "production",
      match: ["db\\.example\\.invalid"],
      tools: [],
    },
  ],
};

export interface HierarchyWorld extends RuntimeWorld {
  api: Api;
  /** The scratch memory store (a git repo whose HEAD holds `systems/guard.json`). */
  store: string;
  /** Two git checkouts a PM can be launched into. */
  alpha: string;
  beta: string;
  /** The file the probe plugin writes its lines to. */
  probeOut: string;
}

async function git(
  world: { env: Record<string, string> },
  cwd: string,
  args: string[],
): Promise<Finished> {
  const identity = [
    "-c",
    "user.name=e2e",
    "-c",
    "user.email=e2e@example.invalid",
  ];
  const done = await exec("git", [...identity, ...args], {
    cwd,
    env: world.env,
    timeoutMs: GIT_TIMEOUT_MS,
  });
  if (done.code !== 0) {
    throw new Error(
      `git ${args[0]} exited ${done.code}: ${done.err.slice(-200)}`,
    );
  }
  return done;
}

/** A git checkout with one commit. */
async function makeRepo(
  env: Record<string, string>,
  folder: string,
): Promise<void> {
  await mkdir(folder, { recursive: true });
  await git({ env }, folder, ["init", "-q"]);
  await writeFile(join(folder, "README.md"), "scratch repo\n");
  await git({ env }, folder, ["add", "README.md"]);
  await git({ env }, folder, ["commit", "-q", "-m", "first"]);
}

/** The scratch memory store: `systems/guard.json` committed, so the production guard enforces it. */
async function makeStore(
  env: Record<string, string>,
  store: string,
): Promise<void> {
  await mkdir(join(store, "systems"), { recursive: true });
  await git({ env }, store, ["init", "-q"]);
  await writeFile(
    join(store, "systems", "guard.json"),
    `${JSON.stringify(GUARD, null, 2)}\n`,
  );
  await git({ env }, store, ["add", "systems/guard.json"]);
  await git({ env }, store, ["commit", "-q", "-m", "guard"]);
}

/** `coven opencode serve <action>` in the world; throws on a non-zero exit. */
export async function serve(
  world: RuntimeWorld,
  action: "start" | "stop" | "status",
): Promise<string> {
  const done = await coven(["opencode", "serve", action], {
    cwd: world.work,
    env: world.env,
    timeoutMs: COMMAND_TIMEOUT_MS,
  });
  if (done.code !== 0) {
    throw new Error(
      `serve ${action} exited ${done.code}: ${(done.err || done.out).slice(-300)}`,
    );
  }
  return done.out.trim();
}

/** The world with its profile and repos made and no server running yet. */
export async function buildWorld(
  root: string,
  binary: string,
): Promise<HierarchyWorld> {
  const home = join(root, "home");
  const store = join(home, "coven-memory");
  const seed = {
    env: { HOME: home, PATH: process.env["PATH"] ?? "" },
  };
  // the store must exist before setup: setup reads it into the plugin's options
  await mkdir(home, { recursive: true });
  await makeStore(seed.env, store);
  const base = await makeWorld(root, binary);
  // the runtime world's model answers the runtime scripts; this one answers the directives
  const model = await startFakeModel({
    decide: decideHierarchy,
    mode: "litellm",
    port: 0,
  });
  await base.model.close();
  await pointProviderAt(base.profile, model.port);
  const world = { ...base, model };
  const alpha = join(home, "repos", "alpha");
  const beta = join(home, "repos", "beta");
  await makeRepo(world.env, alpha);
  await makeRepo(world.env, beta);
  // a file outside every repo: a PM reading it meets an ask
  await writeFile(join(home, "outside.txt"), "outside\n");
  const folder = join(world.profile, "plugin", "probe");
  const probeOut = join(home, "probe.jsonl");
  await mkdir(folder, { recursive: true });
  await cp(PROBE, folder, { recursive: true });
  await addPlugin(world.profile, {
    package: folder,
    options: { out: probeOut },
  });
  return { ...world, store, alpha, beta, probeOut, api: apiFor(world.profile) };
}

/** The world, with its server running; the caller closes it with `closeWorld`. */
export async function makeHierarchyWorld(
  root: string,
  binary: string,
): Promise<HierarchyWorld> {
  const world = await buildWorld(root, binary);
  await serve(world, "start");
  return world;
}

/** Stops the server and the model; never throws. */
export async function closeWorld(
  world: HierarchyWorld | undefined,
): Promise<void> {
  if (world === undefined) {
    return;
  }
  await serve(world, "stop").catch(() => "");
  await world.model.close();
}
