/**
 * The hierarchy suite: coven's GM and PM sessions on a real 2.3.2 server, answered by the
 * directive-following fake model. The checks run in order in one world (one server, one GM) and
 * each one prints `ok N - <name>` or the reason it failed; a check that depends on an earlier
 * one failing says so rather than hiding.
 */
import { spawnSync } from "node:child_process";
import { rm } from "node:fs/promises";
import { join } from "node:path";

import type { Outcome } from "../runtime/checks.ts";
import {
  copiesRecord,
  parallelShellProblem,
  serialShellProblem,
} from "./copies.ts";
import * as flow from "./flow.ts";
import * as guards from "./guards.ts";
import * as hidden from "./hidden.ts";
import * as manage from "./manage.ts";
import * as records from "./records.ts";
import * as restart from "./restart.ts";
import type { Run } from "./run.ts";
import { createSession } from "./sessions.ts";
import type { ToolRun } from "./sessions.ts";
import { closeWorld, makeHierarchyWorld } from "./world.ts";
import type { HierarchyWorld } from "./world.ts";

type Step = [title: string, run: (run: Run) => Promise<string | undefined>];

/** Runs one check; a throw is its problem. */
async function settle(
  work: () => Promise<string | undefined>,
): Promise<string | undefined> {
  try {
    return await work();
  } catch (error) {
    return String(error);
  }
}

/** A line that is neither a pass nor a fail: the value is printed as a record and the reason a check is not run. */
function skipped(title: string, reason: string): Outcome {
  return { title: `${title} # SKIP ${reason}`, problem: undefined };
}

/** The steps that need the PM's child share one run of it. */
function childSteps(): Step[] {
  let child: Promise<ToolRun[]> | undefined;
  const tools = (run: Run): Promise<ToolRun[]> =>
    (child ??= guards.childTools(run));
  return [
    [
      "roles: the role variable reaches a PM's shell and its child's and not the GM's; the key and the server password are scrubbed in all three",
      async (run) => {
        const seen = await guards.presenceRuns(run, await tools(run));
        return guards.roleProblem(seen.pm, seen.child, seen.gm);
      },
    ],
    [
      "asks: a PM's read outside its repo, and its child's, become a deny and nothing waits",
      async (run) => guards.askProblem(run, await tools(run)),
    ],
  ];
}

function steps(): Step[] {
  return [
    ["gm: coven_gm_claim marks the session as the GM", flow.claimProblem],
    [
      "launch: coven_pm_launch starts a PM in the repo, marked as a PM with its code",
      flow.launchProblem,
    ],
    [
      "send: a PM's coven_send DONE reaches the GM and wakes it",
      flow.doneProblem,
    ],
    [
      "notice: a PM turn that ends with no message gives the GM exactly one notice",
      flow.noticeProblem,
    ],
    ["send: the GM's coven_send to an idle PM wakes it", flow.wakeIdleProblem],
    [
      "errors: a ToolError comes back as a tool error result and the next call works",
      flow.toolErrorProblem,
    ],
    [
      "pm-permissions: a PM's push to main is refused, the GM's is not refused by a PM guard",
      guards.pushProblem,
    ],
    [
      "prod-guard: a PM's command naming the scratch target is refused, the GM's is not refused by a PM guard",
      guards.targetProblem,
    ],
    [
      "prod-guard: a PM's shell write to the store's systems/guard.json is refused and the file is unchanged",
      guards.storeWriteProblem,
    ],
    [
      "server-guard: opencode api and a read of the serve folder's password are refused for a PM and for the GM",
      guards.serverGuardProblem,
    ],
    ...childSteps(),
    [
      "shell-env: one command and folder run in turn by a PM and the GM, the PM's shell gets the role variable and the GM's does not",
      serialShellProblem,
    ],
    [
      "shell-env: the same two shells at the same time from two locations, the PM's gets the role variable (the GM's result is recorded)",
      parallelShellProblem,
    ],
    [
      "record: which plugin copies see execute.before, create.before and the turn events",
      copiesRecord,
    ],
    [
      "stop: coven_pm_stop of a busy PM interrupts it, the outcome is interrupted and no notice follows",
      manage.stopBusyProblem,
    ],
    [
      "send: a queued prompt wakes an interrupted PM",
      manage.wakeInterruptedProblem,
    ],
    [
      "stop: coven_pm_stop of an idle PM raises no error, sends no notice and does not mute the next turn",
      manage.stopIdleProblem,
    ],
    [
      "resume: a stopped PM is resumed with RESUMED by gm",
      manage.stopResumeProblem,
    ],
    [
      "archive: coven_pm_stop with archive true hides the PM by the coven.archived flag, archived true lists it, and the session is not archived in OpenCode",
      hidden.archiveStopProblem,
    ],
    [
      "archive: coven_pm_resume of a hidden PM clears the flag, lists it again and sends RESUMED by gm",
      hidden.archiveResumeProblem,
    ],
    ["record: session update merges or replaces metadata", records.mergeRecord],
    [
      "record: what a session list item carries (metadata, parentID, outcome)",
      records.listShapeRecord,
    ],
    [
      "record: the error session.get throws for a deleted session",
      records.notFoundRecord,
    ],
    [
      "record: the id field of a waiting ask in permission.list",
      records.askIdRecord,
    ],
    [
      "record: the cwd create.before reports for a shell call with workdir empty",
      records.emptyWorkdirRecord,
    ],
    [
      "record: whether location.directory is normalized",
      records.directoryRecord,
    ],
    [
      "restart: a restart in the middle of a PM's child's turn leaves no stale child busy and no held-back notice",
      restart.midTurnProblem,
    ],
    [
      "restart: after serve stop and start the PM is still listed, its rules still apply, coven_pm_resume reaches it and serve status lists it",
      restart.afterRestartProblem,
    ],
    [
      "record: the session list page size, the limit parameter and the last page's cursor",
      records.pageRecord,
    ],
  ];
}

/** The checks the suite cannot drive on the fake model, named so the output says what was not run. */
const NOT_DRIVEN: [string, string][] = [
  [
    "record: the failed outcome on a list item",
    "the fake model has no reply that fails a turn, so no PM can end failed",
  ],
  [
    "list: coven_pm_list says missing for a PM whose session is gone",
    "a deleted session leaves the list, so the get that fails with not-found can only happen between the list and the get; the pm-api suite runs the plugin's list code over a deleted PM instead",
  ],
];

/** The run of a world: its GM session is made here (the first check claims it), no PM yet. */
export async function makeRun(world: HierarchyWorld): Promise<Run> {
  const tmp = join(world.profile, "tmp");
  const gm = await createSession(world, {
    directory: world.work,
    title: "gm",
    permissions: [
      { action: "shell", resource: "*", effect: "allow" },
      { action: "external_directory", resource: `${tmp}/*`, effect: "allow" },
    ],
  });
  return { world, gm, pms: new Map() };
}

/** Runs the checks in `world`. */
export async function hierarchyChecks(
  world: HierarchyWorld,
): Promise<Outcome[]> {
  const run = await makeRun(world);
  const outcomes: Outcome[] = [];
  for (const [title, check] of steps()) {
    outcomes.push({ title, problem: await settle(() => check(run)) });
  }
  return [
    ...outcomes,
    ...NOT_DRIVEN.map(([title, reason]) => skipped(title, reason)),
  ];
}

/** Ends the `sleep` of the busy PMs, which a stopped server can leave behind: its number is the suite's own. */
function endSleepers(): void {
  spawnSync("pkill", ["-f", manage.LONG_SLEEP]);
}

/** The hierarchy suite for `main.ts`: a world under `<root>/hierarchy`, the checks, then cleanup. */
export async function hierarchySuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "hierarchy");
  let world: HierarchyWorld | undefined;
  try {
    world = await makeHierarchyWorld(root, harness.binary);
    return await hierarchyChecks(world);
  } catch (error) {
    return [{ title: "hierarchy world", problem: String(error) }];
  } finally {
    await closeWorld(world);
    endSleepers();
    await rm(root, { recursive: true, force: true });
  }
}
