/**
 * The pm-api suite: the coven plugin's own PM code against the real 2.3.2 session API, which the
 * unit tests can only fake. A probe plugin in the scratch profile (see `probe/`) makes sessions
 * and runs the plugin's code; the checks read the lines it records. The standalone run covers a
 * full listing, the hidden flag of `coven_pm_stop`, and a PM list over a deleted session. The
 * served run has the harness archive a session over HTTP, which the plugin cannot do, and
 * checks what the plugin then makes of it.
 */
import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { withServer } from "../../harness/server.ts";
import { addPlugin, makeWorld } from "../runtime/profile.ts";
import type { RuntimeWorld } from "../runtime/profile.ts";
import type { Outcome } from "../runtime/checks.ts";
import { runPrompt } from "../runtime/run.ts";
import { found, isDone, linesUntil } from "./lines.ts";
import type { Recorded, ToolOut } from "./lines.ts";

const PROBE = fileURLToPath(new URL("./probe", import.meta.url));
const WAIT_MS = 30_000;
const SERVED_WAIT_MS = 60_000;

type Problem = (all: readonly Recorded[]) => string | undefined;

function listingProblem(lines: readonly Recorded[]): string | undefined {
  const failed = found(lines, "listedError");
  if (failed !== undefined) {
    return `a full listing failed: ${failed}`;
  }
  return found(lines, "holdsBoth") === true
    ? undefined
    : `the full listing did not hold both sessions (${String(found(lines, "listed"))} sessions)`;
}

/** What a tool run said, or that it said nothing. */
function said(out: ToolOut | undefined): string {
  return out?.content ?? out?.error ?? "no answer";
}

/** The stop's text, then what it says of the isolation trees it swept (none run here). */
const STOPPED =
  /^stopped pm-x; hidden from coven_pm_list; the session is not archived in OpenCode; killed 0 isolation tree\(s\)$/;

/** The first of `checks` whose condition fails, as its message. */
function firstFailed(checks: readonly [boolean, string][]): string | undefined {
  return checks.find(([ok]) => !ok)?.[1];
}

/** The hidden flag end to end: stop sets it, the other metadata stays, list shows it on request, resume clears it. */
function flagProblem(lines: readonly Recorded[]): string | undefined {
  const stop = said(found(lines, "stopArchive"));
  const afterStop = JSON.stringify(found(lines, "afterStop"));
  const hiddenLine = String(found(lines, "listAllAfterStop"));
  const resume = said(found(lines, "resume"));
  const afterResume = JSON.stringify(found(lines, "afterResume"));
  const listed = String(found(lines, "listAfterResume"));
  return firstFailed([
    [STOPPED.test(stop), `coven_pm_stop with archive true says: ${stop}`],
    [
      afterStop === '{"flag":true,"role":"pm","code":"x","note":"kept"}',
      `after the stop the metadata is ${afterStop}`,
    ],
    [
      found(lines, "listAfterStop") === null,
      `the default list shows the hidden PM: ${String(found(lines, "listAfterStop"))}`,
    ],
    [
      /^pm-x {2}\S+ {2}archived {2}/.test(hiddenLine),
      `the list with archived true shows: ${hiddenLine}`,
    ],
    [resume === "resumed pm-x", `coven_pm_resume says: ${resume}`],
    [
      afterResume === '{"role":"pm","code":"x","note":"kept"}',
      `after the resume the metadata is ${afterResume}`,
    ],
    [
      /^pm-x {2}\S+ {2}(idle|busy)/.test(listed),
      `the default list after the resume shows: ${listed}`,
    ],
  ]);
}

function goneProblem(lines: readonly Recorded[]): string | undefined {
  const text = found(lines, "listAfterRemove") ?? "no answer";
  return /pm-x {2}\S+ {2}missing$/.test(text)
    ? undefined
    : `the PM list for a deleted PM shows: ${text}`;
}

/** A PM archived over HTTP is out of the default list, and a resume refuses it. */
function servedProblem(lines: readonly Recorded[]): string | undefined {
  const resume = found(lines, "resume");
  const checks: [boolean, string][] = [
    [
      found(lines, "inLiveListing") === false,
      "the live listing still holds the archived session",
    ],
    [
      found(lines, "listDefaultY") === null,
      `the default list shows it: ${String(found(lines, "listDefaultY"))}`,
    ],
    [
      /^pm-z /.test(found(lines, "listDefaultZ") ?? ""),
      "the default list lost the PM that is not archived",
    ],
    [
      /^pm-y {2}\S+ {2}archived in OpenCode$/.test(
        found(lines, "listArchivedY") ?? "",
      ),
      `the list with archived true shows: ${String(found(lines, "listArchivedY"))}`,
    ],
    [
      /archived in OpenCode.*unarchive it there first/.test(
        resume?.error ?? "",
      ),
      `coven_pm_resume says: ${resume?.content ?? resume?.error ?? "no answer"}`,
    ],
  ];
  return checks.find(([ok]) => !ok)?.[1];
}

/** Makes the world and the probe's folder, adds the probe with `options`, and runs `body`; cleans up. */
async function withProbe<T>(
  harness: { binary: string; root: string },
  name: string,
  options: (home: string) => Record<string, unknown>,
  body: (world: RuntimeWorld, out: string) => Promise<T>,
): Promise<T> {
  const root = join(harness.root, name);
  let world: RuntimeWorld | undefined;
  try {
    world = await makeWorld(root, harness.binary);
    const folder = join(world.profile, "plugin", "probe");
    await mkdir(folder, { recursive: true });
    await cp(PROBE, folder, { recursive: true });
    const out = join(world.home, "pm-api.jsonl");
    await addPlugin(world.profile, {
      package: folder,
      options: { out, ...options(world.home) },
    });
    return await body(world, out);
  } finally {
    await world?.model.close();
    await rm(root, { recursive: true, force: true });
  }
}

function show(lines: readonly Recorded[]): void {
  for (const line of lines) {
    console.log(`# probe: ${JSON.stringify(line)}`);
  }
}

async function standaloneLines(harness: {
  binary: string;
  root: string;
}): Promise<Recorded[]> {
  return withProbe(
    harness,
    "pm-api",
    () => ({}),
    async (world, out) => {
      try {
        await runPrompt(world, "PING");
      } catch (error) {
        console.log(`# the run ended: ${String(error).slice(0, 200)}`);
      }
      const lines = await linesUntil(out, isDone, WAIT_MS);
      show(lines);
      return lines;
    },
  );
}

/** The probe's lines after the harness archived the probe's `ready` session over HTTP. */
async function servedLines(harness: {
  binary: string;
  root: string;
}): Promise<Recorded[]> {
  const trigger = (home: string) => join(home, "archived.trigger");
  return withProbe(
    harness,
    "pm-api-served",
    (home) => ({ trigger: trigger(home) }),
    (world, out) =>
      withServer({ cwd: world.work, env: world.env }, async (api) => {
        // a prompt in the work folder starts that location, and with it the probe plugin
        const loader = await api.post("/api/session", {
          title: "loader",
          location: { directory: world.work },
        });
        const loaderID = /ses_[A-Za-z0-9]+/.exec(JSON.stringify(loader))?.[0];
        await api.post(`/api/session/${String(loaderID)}/prompt`, {
          text: "PING",
        });
        const ready = (all: readonly Recorded[]) =>
          found(all, "ready") !== undefined;
        const first = await linesUntil(out, ready, SERVED_WAIT_MS);
        const id = found(first, "ready");
        if (id === undefined) {
          show(first);
          return first;
        }
        await api.patch(`/api/session/${id}`, { archived: true });
        await writeFile(trigger(world.home), "go\n");
        const lines = await linesUntil(out, isDone, SERVED_WAIT_MS);
        show(lines);
        return lines;
      }),
  );
}

function outcomesOf(
  lines: readonly Recorded[],
  checks: readonly [string, Problem][],
): Outcome[] {
  const broken = found(lines, "probeError");
  return checks.map(([title, problem]) => ({
    title,
    problem:
      broken === undefined ? problem(lines) : `the probe broke: ${broken}`,
  }));
}

/** The pm-api suite for `main.ts`. */
export async function pmApiSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  try {
    const standalone = await standaloneLines(harness);
    const served = await servedLines(harness);
    return [
      ...outcomesOf(standalone, [
        [
          "pm-api: a full session listing ends on the null cursor of the page after the last",
          listingProblem,
        ],
        [
          "pm-api: coven_pm_stop with archive hides the PM by a metadata flag, keeps the other metadata, and resume shows it again",
          flagProblem,
        ],
        [
          "pm-api: coven_pm_list shows missing for a PM whose session was deleted",
          goneProblem,
        ],
      ]),
      ...outcomesOf(served, [
        [
          "pm-api: a PM archived over HTTP is out of the default list and coven_pm_resume refuses it",
          servedProblem,
        ],
      ]),
    ];
  } catch (error) {
    return [{ title: "pm-api world", problem: String(error) }];
  }
}
