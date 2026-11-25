/** The guards in served sessions: what a PM is refused and the GM is not, the role variable, the scrub and the asks. */
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { KEY_ENV, PASSWORD_ENV } from "../runtime/content-script.ts";
import { at, itemsOf, serveDir } from "./api.ts";
import {
  ROLE_COUNT,
  completedProblem,
  gmCall,
  pmCall,
  pmId,
  record,
  refusalProblem,
} from "./run.ts";
import type { Run } from "./run.ts";
import {
  infoOf,
  messagesOf,
  quiet,
  toolsIn,
  waitIdleAfter,
} from "./sessions.ts";
import type { Rule, ToolRun } from "./sessions.ts";
import { TARGET_HOST } from "./world.ts";

/** Prints, never a value, how many role variables there are and whether the key and the server password are set. */
export const PRESENCE = `sh -c 'echo "ROLE=[${ROLE_COUNT}] KEY=[\${${KEY_ENV}:+set}] PW=[\${${PASSWORD_ENV}:+set}]"'`;

const PUSH = "git push origin main";
const REACH = `getent hosts ${TARGET_HOST}`;
const OUTSIDE = "outside.txt";
const ASK_TEXT = "a PM may not wait on a prompt";

/** The `ROLE=[..] KEY=[..] PW=[..]` line of a presence run, or its text when it has none. */
export function presence(run: ToolRun): string {
  return (
    /ROLE=\[\w*\] KEY=\[\w*\] PW=\[\w*\]/.exec(run.text)?.[0] ??
    run.text.slice(0, 160)
  );
}

/** A PM's shell command. */
function pmShell(run: Run, command: string, code = "alpha"): Promise<ToolRun> {
  return pmCall(run, code, "shell", { command });
}

/** A command only pm-permissions would refuse (a push to main) is refused in a PM and not by that hook in the GM. */
export async function pushProblem(run: Run): Promise<string | undefined> {
  const refused = refusalProblem(await pmShell(run, PUSH), "pm-permissions");
  if (refused !== undefined) {
    return `PM: ${refused}`;
  }
  const gm = await gmCall(run, "shell", { command: PUSH });
  return /coven (pm-permissions|prod-guard)/.test(gm.text)
    ? `GM: the push was refused by a PM guard: ${gm.text.slice(0, 200)}`
    : undefined;
}

/** A command naming the scratch target is refused by the production guard in a PM and not in the GM. */
export async function targetProblem(run: Run): Promise<string | undefined> {
  const refused = refusalProblem(await pmShell(run, REACH), "prod-guard");
  if (refused !== undefined) {
    return `PM: ${refused}`;
  }
  const gm = await gmCall(run, "shell", { command: REACH });
  return /coven (pm-permissions|prod-guard)/.test(gm.text)
    ? `GM: the command was refused by a PM guard: ${gm.text.slice(0, 200)}`
    : undefined;
}

/** The production guard refuses a PM's shell write to the store's `systems/guard.json`, and the file stays as committed. */
export async function storeWriteProblem(run: Run): Promise<string | undefined> {
  const file = join(run.world.store, "systems", "guard.json");
  const before = await readFile(file, "utf8");
  const result = await pmShell(run, `printf x >> ${file}`);
  const refused = refusalProblem(result, "prod-guard");
  if (refused !== undefined) {
    return refused;
  }
  return (await readFile(file, "utf8")) === before
    ? undefined
    : "guard.json changed";
}

/** The server guard refuses `opencode api` and a read of the serve folder's `password`, in a PM and in the GM. */
export async function serverGuardProblem(
  run: Run,
): Promise<string | undefined> {
  const password = join(serveDir(run.world.profile), "password");
  const label = "opencode-server-guard";
  const checks: [string, ToolRun][] = [
    ["PM opencode api", await pmShell(run, "opencode api /api/session")],
    [
      "PM read password",
      await pmCall(run, "alpha", "read", { path: password }),
    ],
    [
      "GM opencode api",
      await gmCall(run, "shell", { command: "opencode api /api/session" }),
    ],
    ["GM read password", await gmCall(run, "read", { path: password })],
  ];
  for (const [what, result] of checks) {
    const problem = refusalProblem(result, label);
    if (problem !== undefined) {
      return `${what}: ${problem}`;
    }
  }
  return undefined;
}

/** The sessions under `parentID`, from the server's list. */
export async function childrenOf(
  run: Run,
  parentID: string,
): Promise<string[]> {
  const answer = await run.world.api.get(`/api/session?parentID=${parentID}`);
  const data =
    (answer.body as { data?: { id: string }[] } | undefined)?.data ?? [];
  return data.map((child) => child.id);
}

/** The file outside every repo that the asks are about. */
export function outsideFile(run: Run): string {
  return join(run.world.home, OUTSIDE);
}

/**
 * Makes a read of the outside file ask: a rule is added to the PM's session (and its children
 * copy the session's rules). The profile and the PM ruleset ask for nothing here, so a PM tree
 * only meets an ask when a rule says so. What the read does with no rule is recorded first.
 */
async function armAsk(run: Run): Promise<void> {
  const free = await pmCall(run, "alpha", "read", { path: outsideFile(run) });
  record("PM read outside its repo with no ask rule", free.status);
  const id = pmId(run, "alpha");
  const rules =
    (at(await infoOf(run.world, id), "permissions") as Rule[] | undefined) ??
    [];
  const ask: Rule = {
    action: "read",
    resource: outsideFile(run),
    effect: "ask",
  };
  const answer = await run.world.api.patch(`/api/session/${id}`, {
    permissions: [...rules, ask],
  });
  if (answer.status !== 204) {
    throw new Error(`adding the ask rule answered ${answer.status}`);
  }
}

/** Starts a PM's child that prints the presence line and reads a file outside the repo; returns its tool calls. */
export async function childTools(run: Run): Promise<ToolRun[]> {
  await armAsk(run);
  const outside = outsideFile(run);
  const prompt = [
    "BRIEF",
    `DO shell ${JSON.stringify({ command: PRESENCE })}`,
    `DO read ${JSON.stringify({ path: outside })}`,
  ].join("\n");
  const spawned = await pmCall(run, "alpha", "subagent", {
    agent: "implementer",
    description: "probe",
    prompt,
  });
  const problem = completedProblem(spawned);
  if (problem !== undefined) {
    throw new Error(`the PM's subagent call: ${problem}`);
  }
  const [child] = await childrenOf(run, pmId(run, "alpha"));
  if (child === undefined) {
    throw new Error("the PM has no child session");
  }
  await waitIdleAfter(run.world, child, 0);
  await quiet(run.world, child);
  return toolsIn(await messagesOf(run.world, child));
}

/** The role variable reaches a PM's shell and its child's, and not the GM's; key and password are scrubbed in all three. */
export function roleProblem(
  pm: ToolRun,
  child: ToolRun | undefined,
  gm: ToolRun,
): string | undefined {
  const want: [string, ToolRun | undefined, string][] = [
    ["PM", pm, "ROLE=[1] KEY=[] PW=[]"],
    ["child of the PM", child, "ROLE=[1] KEY=[] PW=[]"],
    ["GM", gm, "ROLE=[0] KEY=[] PW=[]"],
  ];
  for (const [who, found, line] of want) {
    if (
      found === undefined ||
      found.status !== "completed" ||
      presence(found) !== line
    ) {
      return `${who}'s shell printed ${found === undefined ? "nothing" : presence(found)}, wanted ${line}`;
    }
  }
  return undefined;
}

/** The presence line from the PM, the child and the GM. */
export async function presenceRuns(run: Run, child: readonly ToolRun[]) {
  return {
    pm: await pmShell(run, PRESENCE),
    child: child.find((tool) => tool.name === "shell"),
    gm: await gmCall(run, "shell", { command: PRESENCE }),
  };
}

/** An ask in a PM tree (the PM's own read of the file the rule asks about, and the child's) is turned into a deny, and nothing waits. */
export async function askProblem(
  run: Run,
  child: readonly ToolRun[],
): Promise<string | undefined> {
  const own = await pmCall(run, "alpha", "read", { path: outsideFile(run) });
  const kid = child.find((tool) => tool.name === "read");
  for (const [who, found] of [
    ["PM", own],
    ["child", kid],
  ] as const) {
    const problem = deniedProblem(found);
    if (problem !== undefined) {
      return `${who}'s read: ${problem}`;
    }
  }
  const waiting = await run.world.api.get(
    `/api/session/${pmId(run, "alpha")}/permission`,
  );
  const asks = itemsOf(waiting.body).length;
  return asks === 0 ? undefined : `${asks} asks are still waiting in the PM`;
}

/** What is wrong with a call that an ask should have turned into a deny, or undefined. */
function deniedProblem(found: ToolRun | undefined): string | undefined {
  if (found === undefined) {
    return "no call was made";
  }
  return found.status === "error" && found.text.includes(ASK_TEXT)
    ? undefined
    : `ended ${found.status}: ${found.text.slice(0, 160)}`;
}
