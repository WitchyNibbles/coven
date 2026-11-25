/** The runtime suite's checks: isolation, the `tool_choice` hook, doctor, deny-secrets, the SDD smoke and the shell env. */
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { VERSION } from "../../../src/version.ts";
import { coven } from "../../harness/coven.ts";
import { strandedText } from "../../harness/fake-model.ts";
import type { RecordedRequest } from "../../harness/fake-model.ts";
import type { Finished } from "../../harness/process.ts";
import { withServer } from "../../harness/server.ts";
import { snapshot, snapshotDiff } from "../../harness/snapshot.ts";
import { makeWorld, setToolChoiceAuto } from "./profile.ts";
import type { RuntimeWorld } from "./profile.ts";
import { sddProblem, secretsProblem, shellEnvProblem } from "./content.ts";
import { runSession } from "./run.ts";
import type { Session } from "./run.ts";

/** One numbered line of output: a check that passed, or the reason it failed. */
export interface Outcome {
  title: string;
  problem: string | undefined;
}

const COMMAND_TIMEOUT_MS = 90_000;
const FOREIGN_SKILL =
  "---\nname: foreign\ndescription: Planted for the doctor.\n---\nx\n";

function idsOf(payload: unknown): string[] {
  const data = (payload as { data?: unknown } | undefined)?.data;
  return Array.isArray(data)
    ? data.map((item) => String((item as { id?: unknown }).id))
    : [];
}

/** The skill and provider ids a fresh server reports after a reload, started in `work`. */
async function listedByServer(world: RuntimeWorld) {
  return withServer({ cwd: world.work, env: world.env }, async (api) => {
    await api.post("/api/location/reload");
    return {
      skills: idsOf(await api.get("/api/skill")),
      providers: idsOf(await api.get("/api/provider")),
    };
  });
}

/** The first thing wrong with what the first session and the server showed, or undefined. */
async function isolationProblem(
  world: RuntimeWorld,
  session: () => Promise<Session>,
): Promise<string | undefined> {
  const roots = [world.home, "/tmp/opencode"];
  const before = await snapshot(roots);
  await session();
  const listed = await listedByServer(world);
  const changes = snapshotDiff(before, await snapshot(roots));
  if (changes.length > 0) {
    return `the run changed files: ${changes.slice(0, 5).join("; ")}`;
  }
  if (listed.skills.includes("leak-e2e")) {
    return "the 1.x skill leak-e2e is listed";
  }
  return listed.providers.includes("onex")
    ? "the 1.x provider onex is listed"
    : undefined;
}

function withTools(requests: readonly RecordedRequest[]) {
  return requests.filter((request) => request.tools > 0);
}

/** What is wrong with the GREEN session, where the hook is on, or undefined. */
function greenProblem(green: Session): string | undefined {
  const ping = green.history.tools.find((tool) => tool.name === "coven_ping");
  if (ping?.status !== "completed") {
    return `coven_ping did not complete (status ${ping?.status ?? "none"}); the reply was: ${green.history.finalText.slice(0, 120)}`;
  }
  if (!ping.output.includes('"covenRoot"') || !ping.output.includes(VERSION)) {
    return `coven_ping returned ${ping.output.slice(0, 120)}`;
  }
  if (!green.history.finalText.startsWith("PING DONE")) {
    return `the final text is ${green.history.finalText.slice(0, 120)}`;
  }
  const sent = withTools(green.requests);
  const bad = sent.filter((request) => request.toolChoice !== "auto");
  return sent.length === 0 || bad.length > 0
    ? `${bad.length} of ${sent.length} requests with tools lacked tool_choice auto`
    : undefined;
}

/** What is wrong with the RED session, where the hook is off, or undefined. */
function redProblem(red: Session): string | undefined {
  if (red.history.tools.some((tool) => tool.name === "coven_ping")) {
    return "coven_ping ran although the hook was off";
  }
  const stranded = strandedText([{ name: "coven_ping", args: {} }]);
  if (!red.history.finalText.includes(stranded)) {
    return `the final text does not hold the stranded call: ${red.history.finalText.slice(0, 120)}`;
  }
  const sent = withTools(red.requests);
  return sent.length === 0 || sent.some((request) => request.hasToolChoice)
    ? "a request with tools carried tool_choice although the hook was off"
    : undefined;
}

async function toolChoiceProblem(
  world: RuntimeWorld,
  green: Session,
): Promise<string | undefined> {
  const problem = greenProblem(green);
  if (problem !== undefined) {
    return `GREEN run: ${problem}`;
  }
  await setToolChoiceAuto(world.profile, []);
  const red = redProblem(await runSession(world));
  return red === undefined ? undefined : `RED run: ${red}`;
}

function setup(world: RuntimeWorld): Promise<Finished> {
  return coven(["opencode", "setup"], {
    cwd: world.work,
    env: world.env,
    timeoutMs: COMMAND_TIMEOUT_MS,
  });
}

async function doctor(world: RuntimeWorld) {
  const done = await coven(["opencode", "doctor"], {
    cwd: world.work,
    env: world.env,
    timeoutMs: COMMAND_TIMEOUT_MS,
  });
  return { code: done.code, lines: done.out.split("\n"), err: done.err };
}

function lineOf(lines: readonly string[], name: string): string {
  return (
    lines.find((line) => line.includes(` ${name}:`)) ?? `(no ${name} line)`
  );
}

function foreignSkill(world: RuntimeWorld): string {
  return join(world.profile, "home", ".agents", "skills", "foreign");
}

/** Back to the profile `setup` writes: the planted skill gone, the hook on again. */
async function restoreProfile(
  world: RuntimeWorld,
): Promise<string | undefined> {
  await rm(foreignSkill(world), { recursive: true, force: true });
  const done = await setup(world);
  return done.code === 0
    ? undefined
    : `setup exited ${done.code}: ${done.err.slice(-200)}`;
}

async function doctorProblem(world: RuntimeWorld): Promise<string | undefined> {
  const restored = await setup(world);
  if (restored.code !== 0) {
    return `setup exited ${restored.code}: ${restored.err.slice(-200)}`;
  }
  const clean = await doctor(world);
  const failed = clean.lines.filter((line) => line.startsWith("fail"));
  if (clean.code !== 0 || failed.length > 0) {
    return `doctor on a good profile: exit ${clean.code}, ${failed.join(" | ")}`;
  }
  await rm(join(world.profile, "plugin", "coven"), {
    recursive: true,
    force: true,
  });
  const gone = await doctor(world);
  const plugin = lineOf(gone.lines, "plugin");
  if (gone.code !== 1 || !plugin.startsWith("fail")) {
    return `doctor without the plugin: exit ${gone.code}, ${plugin}`;
  }
  const again = await setup(world);
  if (again.code !== 0) {
    return `setup exited ${again.code}: ${again.err.slice(-200)}`;
  }
  const skills = foreignSkill(world);
  await mkdir(skills, { recursive: true });
  await writeFile(join(skills, "SKILL.md"), FOREIGN_SKILL);
  const foreign = await doctor(world);
  const line = lineOf(foreign.lines, "skills");
  return foreign.code === 1 &&
    line.startsWith("fail") &&
    line.includes("foreign")
    ? undefined
    : `doctor with a foreign skill: exit ${foreign.code}, ${line}`;
}

async function settle(
  work: () => Promise<string | undefined>,
): Promise<string | undefined> {
  try {
    return await work();
  } catch (error) {
    return String(error);
  }
}

/** Runs `work` once the profile is restored; otherwise says why it cannot run. */
function afterRestore(
  restored: string | undefined,
  work: () => Promise<string | undefined>,
): Promise<string | undefined> {
  return restored === undefined
    ? work()
    : Promise.resolve(`no restored profile: ${restored}`);
}

/** Runs the six checks in order in `world`; `binary` is the 2.3.2 runtime they all use. */
export async function runtimeChecks(
  world: RuntimeWorld,
  binary: string,
): Promise<Outcome[]> {
  const used = { ...world, env: { ...world.env, COVEN_OPENCODE_BIN: binary } };
  let green: Session | undefined;
  const isolation = await settle(() =>
    isolationProblem(used, async () => {
      green = await runSession(used);
      return green;
    }),
  );
  const toolChoice = await settle(() =>
    green === undefined
      ? Promise.resolve("no GREEN session: the isolation run failed")
      : toolChoiceProblem(used, green),
  );
  const doctored = await settle(() => doctorProblem(used));
  const restored = await settle(() => restoreProfile(used));
  const secrets = await settle(() =>
    afterRestore(restored, () => secretsProblem(used)),
  );
  const sdd = await settle(() =>
    afterRestore(restored, () => sddProblem(used)),
  );
  const shellEnv = await settle(() =>
    afterRestore(restored, () => shellEnvProblem(used)),
  );
  return [
    {
      title: "isolation: the 1.x install stays out and unchanged",
      problem: isolation,
    },
    { title: "tool_choice: the hook is needed and works", problem: toolChoice },
    {
      title: "doctor: passes, then names a missing plugin and a foreign skill",
      problem: doctored,
    },
    {
      title: "deny-secrets: read and cat of .env are refused",
      problem: secrets,
    },
    {
      title: "sdd: two role subagents are awaited and each report arrives once",
      problem: sdd,
    },
    {
      title:
        "shell-env: a shell command sees neither the key nor the server password",
      problem: shellEnv,
    },
  ];
}

/** The runtime suite for `main.ts`: a world under `<root>/runtime`, the checks, then cleanup. */
export async function runtimeSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "runtime");
  let world: RuntimeWorld | undefined;
  try {
    world = await makeWorld(root, harness.binary);
    return await runtimeChecks(world, harness.binary);
  } catch (error) {
    return [{ title: "runtime world", problem: String(error) }];
  } finally {
    await world?.model.close();
    await rm(root, { recursive: true, force: true });
  }
}
