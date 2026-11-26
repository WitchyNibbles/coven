/**
 * The isolation probe (a scratch suite, replaced by the real tests later): on the real 2.3.2
 * binary, can a plugin change what a shell tool call runs? A probe plugin changes the shell
 * event's `shell` and `command` in `shell.create.before`, and the tool input in
 * `tool.execute.before`; this suite sends one shell call per case, then prints what the changed
 * command left behind (marker files), what the tool returned and what the session messages hold.
 * Every case "passes" when it ran; the findings are the `#` lines it prints.
 */
import { cp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildWorld, closeWorld, serve } from "../hierarchy/world.ts";
import type { HierarchyWorld } from "../hierarchy/world.ts";
import { createSession, messagesOf, once } from "../hierarchy/sessions.ts";
import type { ToolRun } from "../hierarchy/sessions.ts";
import { addPlugin } from "../runtime/profile.ts";
import type { Outcome } from "../runtime/checks.ts";

const PROBE = fileURLToPath(new URL("./probe", import.meta.url));
const MAX = 700;

/** The wrapper `event.shell` points at: it records how it was called, then runs the real bash. */
function wrapperScript(dir: string): string {
  return `#!/bin/bash
name=shellonly
if printf '%s' "$*" | grep -qi both; then name=both; fi
if printf '%s' "$*" | grep -q SHELLFAIL; then
  echo "WRAPPER-REFUSED: pretend namespaces are off" >&2
  exit 3
fi
marker="${dir}/wrapper-$name.marker"
{
  echo "dollar0=$0"
  echo "argc=$#"
  for arg in "$@"; do printf 'arg=%s\\n' "$arg"; done
  echo "pwd=$(pwd)"
  echo "exe=$(readlink /proc/$$/exe)"
} >> "$marker"
echo "WRAPPER-RAN-$name"
exec /bin/bash "$@"
`;
}

interface Case {
  name: string;
  tag: string;
  pm: boolean;
  /** The command the model sends; the dir is where its original marker would go. */
  command: (dir: string) => string;
  workdir: (world: HierarchyWorld) => string | undefined;
}

const original = (tag: string, name: string) => (dir: string) =>
  `echo ${tag}-ORIG-STDOUT; pwd; touch ${dir}/${name}.orig`;

const beta = (world: HierarchyWorld): string => world.beta;
const alpha = (world: HierarchyWorld): string => world.alpha;
const none = (): undefined => undefined;

const CASES: Case[] = [
  {
    name: "baseline",
    tag: "BASELINE",
    pm: false,
    command: original("BASELINE", "baseline"),
    workdir: beta,
  },
  {
    name: "shellonly",
    tag: "SHELLONLY",
    pm: false,
    command: original("SHELLONLY", "shellonly"),
    workdir: beta,
  },
  {
    name: "cmdonly",
    tag: "CMDONLY",
    pm: false,
    command: original("CMDONLY", "cmdonly"),
    workdir: beta,
  },
  {
    name: "both",
    tag: "BOTH",
    pm: false,
    command: original("BOTH", "both"),
    workdir: beta,
  },
  {
    name: "execargs",
    tag: "EXECARGS",
    pm: false,
    command: original("EXECARGS", "execargs"),
    workdir: beta,
  },
  {
    name: "execreplace",
    tag: "EXECREPLACE",
    pm: false,
    command: original("EXECREPLACE", "execreplace"),
    workdir: beta,
  },
  {
    name: "shellonly-nowd",
    tag: "SHELLONLY",
    pm: false,
    command: (dir) =>
      `echo SHELLONLY-NOWD-STDOUT; pwd; touch ${dir}/shellonly-nowd.orig`,
    workdir: none,
  },
  {
    name: "shellfail",
    tag: "SHELLFAIL",
    pm: false,
    command: original("SHELLFAIL", "shellfail"),
    workdir: beta,
  },
  {
    name: "shellmissing",
    tag: "SHELLMISSING",
    pm: false,
    command: original("SHELLMISSING", "shellmissing"),
    workdir: beta,
  },
  {
    name: "pm-baseline",
    tag: "PMBASE",
    pm: true,
    command: () => "echo PMBASE-STDOUT; pwd",
    workdir: alpha,
  },
  {
    name: "pm-shellonly",
    tag: "SHELLONLY",
    pm: true,
    command: () => "echo SHELLONLY-PM-STDOUT; pwd",
    workdir: alpha,
  },
  {
    name: "pm-cmdonly",
    tag: "CMDONLY",
    pm: true,
    command: () => "echo CMDONLY-PM-STDOUT; pwd",
    workdir: alpha,
  },
  {
    name: "pm-execargs",
    tag: "EXECARGS",
    pm: true,
    command: () => "echo EXECARGS-PM-STDOUT; pwd",
    workdir: alpha,
  },
];

const cut = (text: string): string =>
  text.replaceAll("\n", "\\n").slice(0, MAX);

async function readOr(path: string): Promise<string | undefined> {
  try {
    return await readFile(path, "utf8");
  } catch {
    return undefined;
  }
}

/** The raw state of the last shell tool part of the session, as the messages hold it. */
async function stateOf(
  world: HierarchyWorld,
  session: string,
): Promise<unknown> {
  const parts = (await messagesOf(world, session))
    .filter((message) => message["type"] === "assistant")
    .flatMap(
      (message) =>
        (message["content"] as Record<string, unknown>[] | undefined) ?? [],
    )
    .filter((part) => part["type"] === "tool");
  return parts.at(-1);
}

function print(name: string, label: string, value: string | undefined): void {
  console.log(
    `# [${name}] ${label}: ${value === undefined ? "(none)" : value}`,
  );
}

/** The files a case left in the probe folder: everything but the wrapper and the plugin's own lines. */
async function leftovers(dir: string): Promise<string[]> {
  return (await readdir(dir))
    .filter((file) => file !== "wrapper.sh" && file !== "probe.jsonl")
    .sort();
}

async function runCase(
  world: HierarchyWorld,
  dir: string,
  out: string,
  sessions: { plain: string; pm: string },
  one: Case,
): Promise<void> {
  const session = one.pm ? sessions.pm : sessions.plain;
  for (const file of await leftovers(dir)) {
    await rm(join(dir, file), { force: true });
  }
  const workdir = one.workdir(world);
  const args: Record<string, unknown> = {
    command: one.command(dir),
    ...(workdir === undefined ? {} : { workdir }),
  };
  const seen = ((await readOr(out)) ?? "").length;
  const run: ToolRun = await once(world, session, "shell", args);
  const name = one.name;
  print(name, "sent", cut(JSON.stringify(args)));
  print(name, "status", run.status);
  print(name, "tool result", cut(run.text));
  print(name, "message input", cut(JSON.stringify(run.input)));
  print(
    name,
    "message state",
    cut(JSON.stringify(await stateOf(world, session))),
  );
  for (const file of await leftovers(dir)) {
    const body = file.endsWith(".orig") ? "" : await readOr(join(dir, file));
    print(name, `file ${file}`, cut(body ?? ""));
  }
  const fresh = ((await readOr(out)) ?? "").slice(seen);
  for (const line of fresh.split("\n").filter((text) => text !== "")) {
    print(name, "probe line", cut(line));
  }
}

/** The world with the probe plugin added before the server starts. */
async function makeProbeWorld(
  root: string,
  binary: string,
): Promise<{ world: HierarchyWorld; dir: string; out: string }> {
  const world = await buildWorld(root, binary);
  const dir = join(world.home, "iso");
  const out = join(dir, "probe.jsonl");
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, "wrapper.sh"), wrapperScript(dir), { mode: 0o755 });
  const folder = join(world.profile, "plugin", "isolation-probe");
  await mkdir(folder, { recursive: true });
  await cp(PROBE, folder, { recursive: true });
  await addPlugin(world.profile, { package: folder, options: { out, dir } });
  await serve(world, "start");
  return { world, dir, out };
}

/** The isolation-probe suite for `main.ts`. */
export async function isolationProbeSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "isolation-probe");
  let world: HierarchyWorld | undefined;
  const outcomes: Outcome[] = [];
  try {
    const made = await makeProbeWorld(root, harness.binary);
    world = made.world;
    console.log("# world is up");
    const permissions = [
      { action: "shell", resource: "*", effect: "allow" },
      { action: "external_directory", resource: "*", effect: "allow" },
    ] as const;
    const plain = await createSession(world, {
      directory: world.alpha,
      permissions,
    });
    const pm = await createSession(world, {
      directory: world.alpha,
      agent: "project-manager",
      title: "pm-probe",
      permissions,
      metadata: { coven: { role: "pm", code: "probe" } },
    });
    for (const one of CASES) {
      console.log(`# case ${one.name} starts`);
      try {
        await runCase(world, made.dir, made.out, { plain, pm }, one);
        outcomes.push({
          title: `isolation-probe: ${one.name} ran`,
          problem: undefined,
        });
      } catch (error) {
        console.log(`# case ${one.name} failed: ${String(error)}`);
        outcomes.push({
          title: `isolation-probe: ${one.name} ran`,
          problem: String(error),
        });
      }
    }
    console.log(`# whole probe file:\n${(await readOr(made.out)) ?? ""}`);
  } catch (error) {
    outcomes.push({ title: "isolation-probe world", problem: String(error) });
  } finally {
    await closeWorld(world);
    await rm(root, { recursive: true, force: true });
  }
  return outcomes;
}
