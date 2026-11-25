/**
 * The evaluate suite: what the plugin code relies on from a real 2.3.2 server and the types do
 * not say. A probe plugin in the scratch profile records, for the shell ask of one session, the
 * field names of the `permission.hook("evaluate")` event and which of them holds the session id,
 * whether `permission.list` answers with a list, and the session's `time` fields when its turn
 * ends. The suite prints each recorded line (names and flags, never values) and checks them.
 */
import { cp, mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { addPlugin, makeWorld } from "../runtime/profile.ts";
import type { RuntimeWorld } from "../runtime/profile.ts";
import type { Outcome } from "../runtime/checks.ts";
import { runPrompt } from "../runtime/run.ts";

const PROBE = fileURLToPath(new URL("./probe", import.meta.url));

interface Recorded {
  evaluate?: string[];
  effect?: string;
  action?: string;
  sessionFields?: string[];
  askList?: boolean;
  timeKeys?: string[];
  idleAfterBusy?: boolean;
  error?: string;
}

function parseLines(text: string): Recorded[] {
  return text
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as Recorded);
}

/** The recorded lines, or none when the probe wrote nothing. */
async function recorded(out: string): Promise<Recorded[]> {
  try {
    return parseLines(await readFile(out, "utf8"));
  } catch {
    return [];
  }
}

/** What is wrong with the ask event the probe saw, or undefined. */
function askProblem(lines: readonly Recorded[]): string | undefined {
  const ask = lines.find((line) => line.effect === "ask");
  if (ask === undefined) {
    return `no evaluate event with effect ask (${lines.length} lines)`;
  }
  return !(ask.sessionFields ?? []).includes("sessionID")
    ? `the ask event has no sessionID field holding a session id; fields: ${(ask.evaluate ?? []).join(", ")}`
    : undefined;
}

/** What is wrong with the session time the probe saw at the end of a turn, or undefined. */
function timeProblem(lines: readonly Recorded[]): string | undefined {
  const time = lines.find((line) => line.timeKeys !== undefined);
  const keys = time?.timeKeys ?? [];
  if (!keys.includes("busy") || !keys.includes("idle")) {
    return `the session time at the end of a turn has ${keys.join(", ") || "no fields"}, not busy and idle`;
  }
  return time?.idleAfterBusy === true
    ? undefined
    : "the session's idle time is not after its busy time at the end of a turn";
}

/** The first thing wrong with what the probe saw, or undefined. */
function problemWith(lines: readonly Recorded[]): string | undefined {
  const failed = lines.find((line) => line.error !== undefined);
  if (failed !== undefined) {
    return `the probe could not register: ${failed.error}`;
  }
  const listed = lines.some((line) => line.askList === true);
  return (
    askProblem(lines) ??
    (listed ? undefined : "permission.list did not answer with a list") ??
    timeProblem(lines)
  );
}

async function probeProblem(world: RuntimeWorld): Promise<string | undefined> {
  const folder = join(world.profile, "plugin", "probe");
  const out = join(world.home, "evaluate.jsonl");
  await mkdir(folder, { recursive: true });
  await cp(PROBE, folder, { recursive: true });
  await addPlugin(world.profile, { package: folder, options: { out } });
  try {
    await runPrompt(world, "SHELLENV");
  } catch (error) {
    // a shell ask with nobody to answer may end the run badly; what the probe saw is what counts
    console.log(`# the run ended: ${String(error).slice(0, 200)}`);
  }
  const lines = await recorded(out);
  for (const line of lines) {
    console.log(`# probe: ${JSON.stringify(line)}`);
  }
  return problemWith(lines);
}

/** The evaluate suite for `main.ts`: a world under `<root>/evaluate`, the probe, then cleanup. */
export async function evaluateSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "evaluate");
  let world: RuntimeWorld | undefined;
  try {
    world = await makeWorld(root, harness.binary);
    return [
      {
        title:
          "evaluate: the ask event names its session; asks and times are readable",
        problem: await probeProblem(world),
      },
    ];
  } catch (error) {
    return [{ title: "evaluate world", problem: String(error) }];
  } finally {
    await world?.model.close();
    await rm(root, { recursive: true, force: true });
  }
}
