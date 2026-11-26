/** Managing PMs: stop, resume, archive and the list, over a real server. */
import { at } from "./api.ts";
import {
  completedProblem,
  gmCall,
  launch,
  noticesFor,
  pmId,
  record,
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

/** The `sleep` every busy PM runs; the number is unique, so the cleanup can find a stray one. */
export const LONG_SLEEP = "sleep 7311";
const NOTICE_WAIT_MS = 3000;

/** Waits until the session is busy; throws after a minute. */
export async function waitBusy(run: Run, sessionID: string): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (!isBusyInfo(await infoOf(run.world, sessionID))) {
    if (Date.now() > deadline) {
      throw new Error(`session ${sessionID} never turned busy`);
    }
    await sleep(100);
  }
}

/** Launches PM `code` into `repo` with a brief that keeps it busy. */
export async function launchBusy(
  run: Run,
  code: string,
  repo: string,
): Promise<string | undefined> {
  const result = await launch(run, code, repo, [
    ["shell", { command: LONG_SLEEP }],
  ]);
  const problem = completedProblem(result);
  if (problem !== undefined) {
    return problem;
  }
  await waitBusy(run, pmId(run, code));
  return undefined;
}

/** The line of the PM list for `code`, or undefined. */
export async function listLine(
  run: Run,
  code: string,
): Promise<string | undefined> {
  const list = await gmCall(run, "coven_pm_list", {});
  if (list.status !== "completed") {
    throw new Error(
      `coven_pm_list ended ${list.status}: ${list.text.slice(0, 200)}`,
    );
  }
  return list.text.split("\n").find((line) => line.startsWith(`pm-${code} `));
}

/** `coven_pm_stop` on a busy PM interrupts it, ends it as `interrupted` and sends no notice. */
export async function stopBusyProblem(run: Run): Promise<string | undefined> {
  const problem = await launchBusy(run, "delta", run.world.alpha);
  if (problem !== undefined) {
    return problem;
  }
  record(
    "active-shape",
    JSON.stringify((await run.world.api.get("/api/session/active")).body).slice(
      0,
      200,
    ),
  );
  const busy = await listLine(run, "delta");
  if (!busy?.includes("  busy")) {
    return `the list shows a sleeping PM as: ${busy}`;
  }
  const stopped = await gmCall(run, "coven_pm_stop", { code: "delta" });
  const bad = completedProblem(stopped);
  if (bad !== undefined) {
    return bad;
  }
  await waitIdleAfter(run.world, pmId(run, "delta"), 0);
  await quiet(run.world, pmId(run, "delta"));
  const info = await infoOf(run.world, pmId(run, "delta"));
  await settleFor(NOTICE_WAIT_MS);
  const notices = await noticesFor(run, "delta");
  record(
    "outcome on the list item after an interrupt",
    String(at(info, "outcome")),
  );
  if (at(info, "outcome") !== "interrupted") {
    return `the outcome after the stop is ${String(at(info, "outcome"))}`;
  }
  return notices.length === 0
    ? undefined
    : `a notice followed the stop: ${notices[0]}`;
}

/** A queued prompt wakes a PM whose turn was interrupted. */
export async function wakeInterruptedProblem(
  run: Run,
): Promise<string | undefined> {
  const id = pmId(run, "delta");
  const sent = await gmCall(run, "coven_send", {
    to: "delta",
    text: "TASK again",
  });
  const problem = completedProblem(sent);
  if (problem !== undefined) {
    return problem;
  }
  await quiet(run.world, id);
  const messages = await messagesOf(run.world, id);
  const index = messages.findIndex(
    (message) => message["text"] === "[from gm] TASK again",
  );
  const answered = messages
    .slice(index + 1)
    .some((message) => message["type"] === "assistant");
  const outcome = at(await infoOf(run.world, id), "outcome");
  return index >= 0 && answered && outcome === "succeeded"
    ? undefined
    : `the interrupted PM ended with ${String(outcome)} (message seen: ${index >= 0}, answered: ${answered})`;
}

/** `coven_pm_stop` on an idle PM: no error, no notice, and its next turn still gives one (the stop mark is cleared). */
export async function stopIdleProblem(run: Run): Promise<string | undefined> {
  const before = (await noticesFor(run, "beta")).length;
  const stopped = await gmCall(run, "coven_pm_stop", { code: "beta" });
  const problem = completedProblem(stopped);
  if (problem !== undefined) {
    return problem;
  }
  await settleFor(NOTICE_WAIT_MS);
  const after = (await noticesFor(run, "beta")).length;
  if (after !== before) {
    return `${after - before} notices followed the stop of an idle PM`;
  }
  const sent = await gmCall(run, "coven_send", {
    to: "beta",
    text: "TASK after the stop",
  });
  const bad = completedProblem(sent);
  if (bad !== undefined) {
    return bad;
  }
  await quiet(run.world, pmId(run, "beta"));
  await settleFor(NOTICE_WAIT_MS);
  const later = (await noticesFor(run, "beta")).length;
  return later === before + 1
    ? undefined
    : `the turn after the stop gave ${later - before} notices, not 1`;
}

/** Stop of a live (not archived) PM and then resume. */
export async function stopResumeProblem(run: Run): Promise<string | undefined> {
  const stopped = await gmCall(run, "coven_pm_stop", { code: "beta" });
  const resumed = await gmCall(run, "coven_pm_resume", {
    code: "beta",
    text: "carry on",
  });
  const bad = completedProblem(stopped) ?? completedProblem(resumed);
  if (bad !== undefined) {
    return bad;
  }
  await settlePm(run, "beta");
  const messages = await messagesOf(run.world, pmId(run, "beta"));
  return messages.some(
    (message) => message["text"] === "RESUMED by gm\ncarry on",
  )
    ? undefined
    : "the resumed PM never received the RESUMED text";
}
