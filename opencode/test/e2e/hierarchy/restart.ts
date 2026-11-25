/** A server restart: the PM stays listed and keeps its rules, and a turn the restart cut off leaves no stale mark. */
import { childrenOf, outsideFile } from "./guards.ts";
import { LONG_SLEEP, listLine, waitBusy } from "./manage.ts";
import {
  completedProblem,
  gmCall,
  launch,
  noticesFor,
  pmCall,
  pmId,
  record,
  serveLog,
  settleFor,
  settlePm,
} from "./run.ts";
import type { Run } from "./run.ts";
import {
  infoOf,
  isBusyInfo,
  messagesOf,
  quiet,
  sleep,
  waitIdleAfter,
} from "./sessions.ts";
import { serve } from "./world.ts";

const NOTICE_WAIT_MS = 3000;
const REAP_WAIT_MS = 2000;

/**
 * `coven opencode serve stop` and then `start`. A `stop` that fails is returned as a problem and
 * the start still runs, so the checks after it have a server to look at: a server that has
 * died but is not yet reaped makes `stop` fail with a traceback while the server is gone.
 */
export async function restart(run: Run): Promise<string | undefined> {
  let failure: string | undefined;
  try {
    await serve(run.world, "stop");
  } catch (error) {
    failure = `serve stop failed: ${String(error).replace(/\s+/g, " ").slice(0, 300)}`;
    await sleep(REAP_WAIT_MS);
  }
  await serve(run.world, "start");
  return failure;
}

/** How many lines of the serve log say a notice was held back for `code`. */
function heldBack(log: string, code: string): number {
  return log
    .split("\n")
    .filter((line) => line.includes(`held back for pm-${code}`)).length;
}

/** The PM's next turn after the restart ends with exactly one notice and holds none back. */
async function noticeAfterRestart(
  run: Run,
  id: string,
): Promise<string | undefined> {
  const woke = await gmCall(run, "coven_send", {
    to: "epsilon",
    text: "TASK a quiet turn",
  });
  const bad = completedProblem(woke);
  if (bad !== undefined) {
    return bad;
  }
  await quiet(run.world, id);
  await settleFor(NOTICE_WAIT_MS);
  const notices = await noticesFor(run, "epsilon");
  // the restart starts a new serve log, so every held-back line in it was made after the restart
  const held = heldBack(await serveLog(run), "epsilon");
  if (notices.length !== 1) {
    return `${notices.length} notices for pm-epsilon after the restart (held back lines after it: ${held}), wanted 1`;
  }
  return held === 0
    ? undefined
    : `${held} notices were held back after the restart`;
}

/**
 * A PM whose child is busy ends its turn (the notice is held back). The server restarts. After
 * it, the PM's next turn ends and its notice goes out: the old child's busy mark is a turn the
 * restart cut off, not work in progress.
 */
export async function midTurnProblem(run: Run): Promise<string | undefined> {
  const sleeper = {
    agent: "implementer",
    description: "sleeper",
    prompt: `BRIEF\nDO shell ${JSON.stringify({ command: LONG_SLEEP })}`,
  };
  const problem = completedProblem(
    await launch(run, "epsilon", run.world.alpha, [["subagent", sleeper]]),
  );
  if (problem !== undefined) {
    return problem;
  }
  const id = pmId(run, "epsilon");
  await waitIdleAfter(run.world, id, 0);
  await quiet(run.world, id);
  const [child] = await childrenOf(run, id);
  if (child === undefined) {
    return "the PM has no child session";
  }
  await waitBusy(run, child);
  await settleFor(NOTICE_WAIT_MS);
  record(
    "held back before the restart",
    String(heldBack(await serveLog(run), "epsilon")),
  );
  const stopFailure = await restart(run);
  record(
    "child busy after the restart",
    String(isBusyInfo(await infoOf(run.world, child))),
  );
  const line = await listLine(run, "epsilon");
  if (line === undefined || line.includes("child busy")) {
    return `after the restart the list shows: ${String(line)}`;
  }
  return (await noticeAfterRestart(run, id)) ?? stopFailure;
}

/** What the PM's rules answer for a shell, a push and a read the session rules ask about. */
async function rulesProblem(run: Run): Promise<string | undefined> {
  const quietShell = await pmCall(run, "alpha", "shell", {
    command: "echo after-restart",
  });
  const push = await pmCall(run, "alpha", "shell", {
    command: "git push origin main",
  });
  const outside = await pmCall(run, "alpha", "read", {
    path: outsideFile(run),
  });
  if (quietShell.status !== "completed") {
    return `a shell in the PM ended ${quietShell.status}: ${quietShell.text.slice(0, 160)}`;
  }
  if (push.status !== "error" || !push.text.includes("coven pm-permissions")) {
    return `the push was ${push.status}: ${push.text.slice(0, 160)}`;
  }
  return outside.status === "error" &&
    outside.text.includes("a PM may not wait on a prompt")
    ? undefined
    : `a read that asks was ${outside.status}: ${outside.text.slice(0, 160)}`;
}

/** `coven_pm_resume` after a restart reaches the PM: it takes the RESUMED text as a turn. */
async function resumeProblem(run: Run): Promise<string | undefined> {
  const resumed = await gmCall(run, "coven_pm_resume", {
    code: "alpha",
    text: "back after the restart",
  });
  const problem = completedProblem(resumed);
  if (problem !== undefined) {
    return problem;
  }
  await settlePm(run, "alpha");
  const messages = await messagesOf(run.world, pmId(run, "alpha"));
  return messages.some(
    (message) => message["text"] === "RESUMED by gm\nback after the restart",
  )
    ? undefined
    : "the PM never received the RESUMED text after the restart";
}

/**
 * After `serve stop` and `start` the PM is still listed, its session rules still apply, a resume
 * reaches it, and `serve status` lists it.
 */
export async function afterRestartProblem(
  run: Run,
): Promise<string | undefined> {
  const stopFailure = await restart(run);
  const line = await listLine(run, "alpha");
  if (line === undefined) {
    return stopFailure ?? "the list does not show pm-alpha after the restart";
  }
  const problem = (await rulesProblem(run)) ?? (await resumeProblem(run));
  if (problem !== undefined) {
    return problem;
  }
  const status = (await serve(run.world, "status")).split("\n");
  record("serve status lines", String(status.length));
  return status.some((each) => each.startsWith("pm-alpha "))
    ? stopFailure
    : `serve status shows: ${status.join(" | ")}`;
}
