/**
 * The served group: deny-secrets in a PM session on `coven opencode serve`, the same probes as
 * the runtime group (a read of `.env` and a `cat .env`), but against a session made over the
 * API on the long-running server rather than a one-shot run. The PM is marked by metadata only,
 * with no session rules, so the refusal can only come from coven's plugin.
 */
import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { at } from "../hierarchy/api.ts";
import {
  createSession,
  infoOf,
  messagesOf,
  once,
} from "../hierarchy/sessions.ts";
import type { ToolRun } from "../hierarchy/sessions.ts";
import { closeWorld, makeHierarchyWorld } from "../hierarchy/world.ts";
import type { HierarchyWorld } from "../hierarchy/world.ts";
import type { Outcome } from "../runtime/checks.ts";
import { SECRET_VALUE, SECRETS_FILE } from "../runtime/content-script.ts";

const REFUSAL = "coven deny-secrets:";

/** What is wrong with a call that should have been refused by deny-secrets, or undefined. */
function refusalProblem(call: ToolRun): string | undefined {
  const refused = call.status === "error" && call.text.includes(REFUSAL);
  return refused
    ? undefined
    : `${call.name} was ${call.status} with ${call.text.slice(0, 160)}`;
}

/** One check of the served group: a title and what it finds wrong in the PM session. */
type Check = [
  title: string,
  problem: (world: HierarchyWorld, pm: string) => Promise<string | undefined>,
];

const CHECKS: Check[] = [
  [
    "served: a PM session on the server is refused a read of .env by deny-secrets",
    async (world, pm) =>
      refusalProblem(await once(world, pm, "read", { path: SECRETS_FILE })),
  ],
  [
    "served: a PM session on the server is refused a shell cat of .env by deny-secrets",
    async (world, pm) =>
      refusalProblem(
        await once(world, pm, "shell", { command: `cat ${SECRETS_FILE}` }),
      ),
  ],
  [
    "served: the secret value is in no message of the PM session",
    async (world, pm) =>
      JSON.stringify(await messagesOf(world, pm)).includes(SECRET_VALUE)
        ? "the secret value is in the session's messages"
        : undefined,
  ],
];

/** Runs the checks in `world`: one PM session made over the API, in the repo that holds the secret file. */
export async function servedChecks(world: HierarchyWorld): Promise<Outcome[]> {
  await writeFile(
    join(world.alpha, SECRETS_FILE),
    `API_TOKEN=${SECRET_VALUE}\n`,
  );
  const pm = await createSession(world, {
    directory: world.alpha,
    agent: "project-manager",
    title: "pm-served",
    metadata: { coven: { role: "pm", code: "served" } },
  });
  const made = at(await infoOf(world, pm), "metadata", "coven", "role");
  const outcomes: Outcome[] = [];
  for (const [title, problem] of CHECKS) {
    try {
      outcomes.push({
        title,
        problem:
          made === "pm"
            ? await problem(world, pm)
            : "the PM session was not made with the pm role",
      });
    } catch (error) {
      outcomes.push({ title, problem: String(error) });
    }
  }
  return outcomes;
}

/** The served suite for `main.ts`: a world under `<root>/served`, the checks, then cleanup. */
export async function servedSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "served");
  let world: HierarchyWorld | undefined;
  try {
    world = await makeHierarchyWorld(root, harness.binary);
    return await servedChecks(world);
  } catch (error) {
    return [{ title: "served world", problem: String(error) }];
  } finally {
    await closeWorld(world);
    await rm(root, { recursive: true, force: true });
  }
}
