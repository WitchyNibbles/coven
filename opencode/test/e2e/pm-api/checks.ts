/**
 * The pm-api suite: the coven plugin's own PM code against the real 2.3.2 `ctx.session`, which
 * the unit tests can only fake. A probe plugin in the scratch profile (see `probe/index.ts`)
 * makes a GM and a PM session and records what a full listing, an archive through `update` and
 * `coven_pm_stop`, and a PM list over a deleted session do. The checks read those lines.
 */
import { cp, mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { addPlugin, makeWorld } from "../runtime/profile.ts";
import type { RuntimeWorld } from "../runtime/profile.ts";
import type { Outcome } from "../runtime/checks.ts";
import { runPrompt } from "../runtime/run.ts";

const PROBE = fileURLToPath(new URL("./probe", import.meta.url));
const WAIT_MS = 30_000;
const POLL_MS = 250;

interface Tool {
  content?: string;
  error?: string;
}

interface Recorded {
  listed?: number;
  holdsBoth?: boolean;
  listedError?: string;
  updateAnswered?: string;
  archivedAfterUpdate?: boolean;
  stopArchive?: Tool;
  listAfterRemove?: Tool;
  probeError?: string;
  done?: boolean;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** The probe's lines, once it says it is done (or after `WAIT_MS`). */
async function recordedLines(out: string): Promise<Recorded[]> {
  const deadline = Date.now() + WAIT_MS;
  for (;;) {
    const text = await readFile(out, "utf8").catch(() => "");
    const lines = text
      .split("\n")
      .filter((line) => line.trim() !== "")
      .map((line) => JSON.parse(line) as Recorded);
    const finished = lines.some(
      (line) => line.done === true || line.probeError !== undefined,
    );
    if (finished || Date.now() > deadline) {
      return lines;
    }
    await sleep(POLL_MS);
  }
}

/** The first value of `key` among the lines. */
function found<K extends keyof Recorded>(
  lines: readonly Recorded[],
  key: K,
): Recorded[K] | undefined {
  return lines.find((line) => line[key] !== undefined)?.[key];
}

function listingProblem(lines: readonly Recorded[]): string | undefined {
  const failed = found(lines, "listedError");
  if (failed !== undefined) {
    return `a full listing failed: ${failed}`;
  }
  return found(lines, "holdsBoth") === true
    ? undefined
    : `the full listing did not hold both sessions (${String(found(lines, "listed"))} sessions)`;
}

function archiveProblem(lines: readonly Recorded[]): string | undefined {
  if (found(lines, "archivedAfterUpdate") !== false) {
    // 2.3.2 ignores `archived` in the plugin's update; if that changes, the plugin can archive
    return `update with archived true now archives (answered ${String(found(lines, "updateAnswered"))}): coven_pm_stop could archive for real`;
  }
  const stop = found(lines, "stopArchive");
  const said = stop?.content ?? stop?.error ?? "no answer";
  return stop?.error !== undefined &&
    /did not archive/.test(stop.error) &&
    !/archived it/.test(stop.error)
    ? undefined
    : `coven_pm_stop with archive true says: ${said}`;
}

function goneProblem(lines: readonly Recorded[]): string | undefined {
  const shown = found(lines, "listAfterRemove");
  const text = shown?.content ?? shown?.error ?? "no answer";
  return /pm-x {2}\S+ {2}missing$/.test(text)
    ? undefined
    : `the PM list for a deleted PM shows: ${text}`;
}

async function outcomes(world: RuntimeWorld): Promise<Outcome[]> {
  const folder = join(world.profile, "plugin", "probe");
  const out = join(world.home, "pm-api.jsonl");
  await mkdir(folder, { recursive: true });
  await cp(PROBE, folder, { recursive: true });
  await addPlugin(world.profile, { package: folder, options: { out } });
  try {
    await runPrompt(world, "PING");
  } catch (error) {
    console.log(`# the run ended: ${String(error).slice(0, 200)}`);
  }
  const lines = await recordedLines(out);
  for (const line of lines) {
    console.log(`# probe: ${JSON.stringify(line)}`);
  }
  const broken = found(lines, "probeError");
  const check = (
    title: string,
    problem: (all: readonly Recorded[]) => string | undefined,
  ): Outcome => ({
    title,
    problem:
      broken === undefined ? problem(lines) : `the probe broke: ${broken}`,
  });
  return [
    check(
      "pm-api: a full session listing ends on the null cursor of the page after the last",
      listingProblem,
    ),
    check(
      "pm-api: update archived is a no-op, and coven_pm_stop with archive says it did not archive",
      archiveProblem,
    ),
    check(
      "pm-api: coven_pm_list shows missing for a PM whose session was deleted",
      goneProblem,
    ),
  ];
}

/** The pm-api suite for `main.ts`: a world under `<root>/pm-api`, the probe, then cleanup. */
export async function pmApiSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "pm-api");
  let world: RuntimeWorld | undefined;
  try {
    world = await makeWorld(root, harness.binary);
    return await outcomes(world);
  } catch (error) {
    return [{ title: "pm-api world", problem: String(error) }];
  } finally {
    await world?.model.close();
    await rm(root, { recursive: true, force: true });
  }
}
