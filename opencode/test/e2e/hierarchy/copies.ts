/** Event identity across plugin locations: parallel shells with one command and folder, from a PM and the GM. */
import { mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";

import { ROLE_COUNT, gmCall, pmCall, pmId, record } from "./run.ts";
import type { Run } from "./run.ts";
import type { ToolRun } from "./sessions.ts";

interface Line {
  copy: string;
  kind: string;
  tool?: string;
  sessionID?: string;
  command?: string;
  cwd?: string;
}

const COMMAND = `sh -c 'sleep 1; echo "R=[${ROLE_COUNT}]"'`;
const PM_WANTS = "R=[1]";
const GM_WANTS = "R=[0]";

/** The lines the probe plugin wrote. */
export async function probeLines(run: Run): Promise<Line[]> {
  const text = await readFile(run.world.probeOut, "utf8").catch(() => "");
  return text
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as Line);
}

/** `R=[<count>]`, or the start of the text when the run printed neither. */
function roleOf(result: ToolRun): string {
  return (
    /R=\[\w*\]/.exec(result.text)?.[0] ??
    `${result.status}: ${result.text.slice(0, 100)}`
  );
}

/** The folder both sessions run in: under the profile's `tmp/`, which the PM and the GM may both reach. */
async function sharedFolder(run: Run): Promise<string> {
  const folder = join(run.world.profile, "tmp", "shared");
  await mkdir(folder, { recursive: true });
  return folder;
}

/** The names of the plugin copies (locations) that saw each line of `kind` for `match`. */
function copiesSeeing(
  lines: readonly Line[],
  kind: string,
  match: (line: Line) => boolean,
): string[] {
  return [
    ...new Set(
      lines
        .filter((line) => line.kind === kind && match(line))
        .map((line) => line.copy),
    ),
  ].sort();
}

/**
 * The PM's and the GM's shell run one after the other with the same command and folder: the PM's
 * gets the role variable and the GM's does not.
 */
export async function serialShellProblem(
  run: Run,
): Promise<string | undefined> {
  const workdir = await sharedFolder(run);
  const pm = roleOf(
    await pmCall(run, "alpha", "shell", { command: COMMAND, workdir }),
  );
  const gm = roleOf(await gmCall(run, "shell", { command: COMMAND, workdir }));
  return pm === PM_WANTS && gm === GM_WANTS
    ? undefined
    : `PM printed ${pm}, GM printed ${gm}`;
}

/**
 * The same two shells at the same time, from two locations: the PM's shell must get the role
 * variable. The GM's result is recorded: the shell queue gives a PM to a shell when the calls
 * under one key disagree (`shell-env.ts`), so a GM shell racing a PM shell may get it too.
 */
export async function parallelShellProblem(
  run: Run,
): Promise<string | undefined> {
  const workdir = await sharedFolder(run);
  const [pmRun, gmRun] = await Promise.all([
    pmCall(run, "alpha", "shell", { command: COMMAND, workdir }),
    gmCall(run, "shell", { command: COMMAND, workdir }),
  ]);
  const pm = roleOf(pmRun);
  const gm = roleOf(gmRun);
  record("parallel-shells", `PM ${pm}, GM ${gm}`);
  if (pm !== PM_WANTS) {
    return `the PM's shell printed ${pm}`;
  }
  return gm === GM_WANTS
    ? undefined
    : `the GM's shell printed ${gm}: the PM's role was given to both shells racing under one key`;
}

/** Records which plugin copies saw a PM's and the GM's tool calls and shell starts. */
export async function copiesRecord(run: Run): Promise<string | undefined> {
  const lines = await probeLines(run);
  const pm = pmId(run, "alpha");
  const seen = (sessionID: string) =>
    copiesSeeing(
      lines,
      "execute",
      (line) => line.sessionID === sessionID && line.tool === "shell",
    );
  const shells = copiesSeeing(
    lines,
    "create",
    (line) => line.command?.startsWith("sh -c 'sleep 1") === true,
  );
  const names = (list: string[]) =>
    list.map((dir) => dir.split("/").slice(-2).join("/")).join(", ");
  record("execute.before copies, PM shell", names(seen(pm)));
  record("execute.before copies, GM shell", names(seen(run.gm)));
  record("create.before copies, shell start", names(shells));
  const events = copiesSeeing(lines, "event", (line) => line.sessionID === pm);
  record("event copies, PM turn", names(events));
  return seen(pm).length === 0
    ? "no plugin copy saw the PM's shell call"
    : undefined;
}
