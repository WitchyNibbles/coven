/**
 * The isolation suite: a PM's shell on the real 2.3.2 server runs in its own user, PID and mount
 * namespaces, and the GM's shell does not. The world is the hierarchy suite's, started with the
 * wrapper's two test seams in the server's environment (a fake tree that would change what the
 * wrapper does if it honoured them) to show that no PM shell sees them. One facts script runs in
 * the PM's shell and in the GM's; each check reads its own lines of what they printed.
 */
import { mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Outcome } from "../runtime/checks.ts";
import { makeRun } from "../hierarchy/checks.ts";
import { serveDir } from "../hierarchy/api.ts";
import { gmCall, launch, pmCall, settlePm } from "../hierarchy/run.ts";
import type { Run } from "../hierarchy/run.ts";
import { buildWorld, closeWorld, serve } from "../hierarchy/world.ts";
import type { HierarchyWorld } from "../hierarchy/world.ts";
import { fact, factsOf, writeFacts } from "./facts.ts";
import type { Facts, Places } from "./facts.ts";
import { serveWriteProblem } from "./files.ts";
import { interruptProblem, timeoutProblem } from "./timeout.ts";

/** The names the fake tree is reached by: the wrapper's seams. */
const SYS_SEAM = "COVEN_ISOLATE_SYS";
const PATH_SEAM = "COVEN_ISOLATE_PATH";
/** What the fake `unshare` leaves when anything runs it. */
const MARKER = "fake-unshare-ran";
const SYSTEM_TOOLS = ["id", "mktemp", "rm", "tr", "mount", "sh"];

/** What the two sessions' shells printed, and where things are. */
interface Seen {
  places: Places;
  pm: Facts;
  gm: Facts;
  fake: string;
  repo: string;
  workdir: string;
}

type Step = [
  title: string,
  run: (run: Run, seen: Seen) => Promise<string | undefined>,
];

/**
 * A tree for the seams that would break isolation if the wrapper took it: a system tree that says
 * user namespaces are off, and a PATH whose `unshare` runs nothing and leaves a marker. The other
 * tools the wrapper needs are links to the system's, so that the fake `unshare` is the only
 * difference.
 */
async function fakeTree(root: string): Promise<{ sys: string; path: string }> {
  const sys = join(root, "sys");
  const path = join(root, "bin");
  await mkdir(join(sys, "user"), { recursive: true });
  await writeFile(join(sys, "user", "max_user_namespaces"), "0\n");
  await mkdir(path, { recursive: true });
  for (const tool of SYSTEM_TOOLS) {
    await symlink(`/usr/bin/${tool}`, join(path, tool));
  }
  const unshare = join(path, "unshare");
  await writeFile(
    unshare,
    `#!/bin/sh\necho ran >> ${join(root, MARKER)}\ncase "$*" in *--help*) echo "--map-user" ;; esac\nexit 0\n`,
    { mode: 0o755 },
  );
  return { sys, path };
}

/** The server's pid, from the folder the harness may read. */
async function serverPid(world: HierarchyWorld): Promise<number> {
  const text = await readFile(join(serveDir(world.profile), "pid"), "utf8");
  const pid = Number.parseInt(text.trim(), 10);
  if (!Number.isInteger(pid) || pid <= 0) {
    throw new Error(`the pid file holds ${JSON.stringify(text)}`);
  }
  return pid;
}

/** The text a shell call returned; throws when the call did not complete. */
function outputOf(call: { status: string; text: string }, who: string): string {
  if (call.status !== "completed") {
    throw new Error(
      `${who}'s facts shell ended ${call.status}: ${call.text.slice(0, 300)}`,
    );
  }
  return call.text;
}

/** Makes the GM and the PM, runs the facts script in each shell and returns what they printed. */
async function gather(run: Run, fake: string): Promise<Seen> {
  const { world } = run;
  const places: Places = {
    serverPid: await serverPid(world),
    serve: serveDir(world.profile),
    data: join(world.profile, "data"),
  };
  const folder = join(world.profile, "tmp", "isolation");
  const workdir = world.alpha;
  const pmScript = await writeFacts(folder, "pm.sh", places, true);
  const gmScript = await writeFacts(folder, "gm.sh", places, false);
  const claim = await gmCall(run, "coven_gm_claim", {});
  const launched = await launch(run, "alpha", world.alpha, []);
  if (claim.status !== "completed" || launched.status !== "completed") {
    throw new Error(
      `claim ${claim.status}, launch ${launched.status}: ${launched.text.slice(0, 200)}`,
    );
  }
  await settlePm(run, "alpha");
  const pm = await pmCall(run, "alpha", "shell", {
    command: `sh ${pmScript}`,
    workdir,
  });
  const gm = await gmCall(run, "shell", { command: `sh ${gmScript}`, workdir });
  return {
    places,
    pm: factsOf(outputOf(pm, "the PM")),
    gm: factsOf(outputOf(gm, "the GM")),
    fake,
    repo: world.alpha,
    workdir,
  };
}

/** `undefined` when `facts` has `key` equal to `want`, else what it has. */
function expect(facts: Facts, key: string, want: string): string | undefined {
  const got = fact(facts, key);
  return got === want ? undefined : `${key} is [${got}], expected [${want}]`;
}

/** The first of the problems that is not undefined. */
function firstOf(...problems: (string | undefined)[]): string | undefined {
  return problems.find((problem) => problem !== undefined);
}

/** A check on what the two shells printed. */
function onFacts(
  check: (seen: Seen) => string | undefined,
): (run: Run, seen: Seen) => Promise<string | undefined> {
  return (_run, seen) => Promise.resolve(check(seen));
}

/** The server's environment holds the seams: the facts below then mean something. */
async function serverHasSeams(seen: Seen): Promise<string | undefined> {
  const environ = await readFile(
    `/proc/${seen.places.serverPid}/environ`,
    "utf8",
  );
  const names = environ.split("\0").map((entry) => entry.split("=")[0]);
  const missing = [SYS_SEAM, PATH_SEAM].filter((name) => !names.includes(name));
  return missing.length === 0
    ? undefined
    : `the server's environment lacks ${missing.join(" and ")}`;
}

function steps(): Step[] {
  return [
    [
      "setup: the server's pid file names a live process whose environment carries the wrapper's test seams",
      (_run, seen) => serverHasSeams(seen),
    ],
    [
      "gm: the GM's shell sees the server's pid in /proc and can read its environment",
      onFacts(({ gm }) =>
        firstOf(
          expect(gm, "SERVER_IN_PROC", "yes"),
          expect(gm, "ENVIRON", "readable"),
        ),
      ),
    ],
    [
      "gm: the GM's shell lists the serve folder's files and the data folder's",
      onFacts(({ gm }) => {
        const serveList = fact(gm, "SERVE").split(" ");
        const dataList = fact(gm, "DATA").trim();
        return serveList.includes("password") &&
          serveList.includes("pid") &&
          dataList !== "" &&
          !dataList.startsWith("ls:")
          ? undefined
          : `the GM lists SERVE=[${fact(gm, "SERVE")}] DATA=[${dataList}]`;
      }),
    ],
    [
      "gm: the GM's shell has no role variable and is not isolated (git and a write work)",
      onFacts(({ gm }) =>
        firstOf(
          expect(gm, "ROLE", ""),
          expect(gm, "GIT", "ok"),
          expect(gm, "WRITE", "ok"),
        ),
      ),
    ],
    [
      "pm: the server's pid is not in the PM shell's /proc",
      onFacts(({ pm }) =>
        firstOf(
          expect(pm, "SERVER_IN_PROC", "no"),
          Number(fact(pm, "PROCESSES")) <= 10
            ? undefined
            : `the PM's /proc has ${fact(pm, "PROCESSES")} processes`,
        ),
      ),
    ],
    [
      "pm: reading /proc/<server pid>/environ fails in the PM shell",
      onFacts(({ pm }) => expect(pm, "ENVIRON", "failed")),
    ],
    [
      "pm: the serve folder and the data folder list empty in the PM shell",
      onFacts(({ pm }) =>
        firstOf(expect(pm, "SERVE", ""), expect(pm, "DATA", "")),
      ),
    ],
    [
      "pm: git status and a file write in the PM's repo work",
      onFacts(({ pm }) =>
        firstOf(expect(pm, "GIT", "ok"), expect(pm, "WRITE", "ok")),
      ),
    ],
    [
      "pm: the PM shell's cwd is the call's workdir",
      onFacts(({ pm, workdir }) => expect(pm, "CWD", workdir)),
    ],
    [
      "pm: the role variable is set in the PM shell",
      onFacts(({ pm }) => expect(pm, "ROLE", "pm")),
    ],
    [
      "pm: umount of /proc and of the serve folder fail, and the serve folder still lists empty",
      onFacts(({ pm }) =>
        firstOf(
          expect(pm, "UMOUNT_PROC", "failed"),
          expect(pm, "UMOUNT_SERVE", "failed"),
          expect(pm, "SERVE_AFTER", ""),
          expect(pm, "SERVER_AFTER", "no"),
        ),
      ),
    ],
    [
      "seams: the PM shell sees neither test seam, the fake tree was never used and the shell is isolated all the same",
      async (_run, seen) => {
        const used = await readFile(join(seen.fake, MARKER), "utf8").catch(
          () => "",
        );
        return firstOf(
          expect(seen.pm, "SEAMS", "0"),
          used === "" ? undefined : "the fake unshare ran",
          expect(seen.pm, "SERVER_IN_PROC", "no"),
          expect(seen.pm, "SERVE", ""),
        );
      },
    ],
    [
      "timeout: a PM shell call past its timeout leaves no process behind, or (2.3.2) is moved to the background with nothing killed",
      (run, seen) => timeoutProblem(run, seen.workdir),
    ],
    [
      "interrupt: a PM shell call killed by OpenCode (coven_pm_stop of a busy PM) leaves no process behind",
      (run) => interruptProblem(run, run.world.beta),
    ],
    [
      "write-guard: a PM's Write and Edit into the serve folder, the wrapper copy and the password are refused, and the files are unchanged",
      (run, seen) => serveWriteProblem(run, seen.places.serve, seen.repo),
    ],
  ];
}

/** A problem as a thrown or returned text. */
async function settle(
  work: () => Promise<string | undefined>,
): Promise<string | undefined> {
  try {
    return await work();
  } catch (error) {
    return String(error);
  }
}

/** Runs the checks in `world`, which is started with the seams in its env. */
async function isolationChecks(
  world: HierarchyWorld,
  fake: string,
): Promise<Outcome[]> {
  const run = await makeRun(world);
  const seen = await gather(run, fake);
  const outcomes: Outcome[] = [];
  for (const [title, check] of steps()) {
    outcomes.push({ title, problem: await settle(() => check(run, seen)) });
  }
  return outcomes;
}

/** The isolation suite for `main.ts`: the hierarchy world with the seams in the server's env. */
export async function isolationSuite(harness: {
  binary: string;
  root: string;
}): Promise<Outcome[]> {
  const root = join(harness.root, "isolation");
  let world: HierarchyWorld | undefined;
  try {
    const built = await buildWorld(root, harness.binary);
    const tree = await fakeTree(join(root, "fake"));
    world = {
      ...built,
      env: { ...built.env, [SYS_SEAM]: tree.sys, [PATH_SEAM]: tree.path },
    };
    await serve(world, "start");
    return await isolationChecks(world, join(root, "fake"));
  } catch (error) {
    return [{ title: "isolation world", problem: String(error) }];
  } finally {
    await closeWorld(world);
    await rm(root, { recursive: true, force: true });
  }
}
