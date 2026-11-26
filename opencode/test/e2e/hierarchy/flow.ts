/** The GM and PM message flow: claim, launch, DONE, the turn-end notice, queued wake-ups and tool errors. */
import { at } from "./api.ts";
import {
  completedProblem,
  gmCall,
  gmInbox,
  launch,
  noticesFor,
  pmId,
  record,
  settleFor,
  settlePm,
} from "./run.ts";
import type { Run } from "./run.ts";
import { infoOf, messagesOf, quiet, turn } from "./sessions.ts";

const NOTICE_WAIT_MS = 3000;

/** `coven_gm_claim` marks the GM session. */
export async function claimProblem(run: Run): Promise<string | undefined> {
  const result = await gmCall(run, "coven_gm_claim", {});
  const problem = completedProblem(result);
  if (problem !== undefined) {
    return problem;
  }
  const role = at(await infoOf(run.world, run.gm), "metadata", "coven", "role");
  return role === "gm"
    ? undefined
    : `the session metadata role is ${String(role)}, not gm`;
}

/** `coven_pm_launch` starts a PM in the repo, marked as a PM with its code, and the brief runs. */
export async function launchProblem(run: Run): Promise<string | undefined> {
  const result = await launch(run, "alpha", run.world.alpha, [
    ["coven_send", { text: "DONE brief read" }],
  ]);
  const problem = completedProblem(result);
  if (problem !== undefined) {
    return problem;
  }
  const info = await infoOf(run.world, pmId(run, "alpha"));
  const coven = at(info, "metadata", "coven");
  const where = at(info, "location", "directory");
  if (JSON.stringify(coven) !== JSON.stringify({ role: "pm", code: "alpha" })) {
    return `the PM's metadata coven is ${JSON.stringify(coven)}`;
  }
  return where === run.world.alpha
    ? undefined
    : `the PM works in ${String(where)}`;
}

/** A PM's `coven_send DONE` is queued into the GM, which wakes and answers it. */
export async function doneProblem(run: Run): Promise<string | undefined> {
  await settlePm(run, "alpha");
  await quiet(run.world, run.gm);
  const messages = await messagesOf(run.world, run.gm);
  const index = messages.findIndex(
    (message) => message["text"] === "[from pm-alpha] DONE brief read",
  );
  if (index < 0) {
    return `the GM never received "[from pm-alpha] DONE brief read"; it got ${JSON.stringify(await gmInbox(run))}`;
  }
  const after = messages
    .slice(index + 1)
    .some((message) => message["type"] === "assistant");
  return after ? undefined : "the GM did not take a turn after the message";
}

/** A PM turn that ends with no message gives the GM exactly one notice, however many plugin copies see it. */
export async function noticeProblem(run: Run): Promise<string | undefined> {
  const result = await launch(run, "beta", run.world.beta, []);
  const problem = completedProblem(result);
  if (problem !== undefined) {
    return problem;
  }
  await settlePm(run, "beta");
  await settleFor(NOTICE_WAIT_MS);
  const notices = await noticesFor(run, "beta");
  record("notice", JSON.stringify(notices));
  if (notices.length !== 1) {
    return `${notices.length} notices for pm-beta, not 1`;
  }
  return notices[0]?.includes("ended a turn (succeeded) without a message")
    ? undefined
    : `the notice reads: ${notices[0]}`;
}

/** The GM's `coven_send` to an idle PM queues a turn that wakes it. */
export async function wakeIdleProblem(run: Run): Promise<string | undefined> {
  const id = pmId(run, "beta");
  const sent = await gmCall(run, "coven_send", {
    to: "beta",
    text: "TASK wake up",
  });
  const problem = completedProblem(sent);
  if (problem !== undefined) {
    return problem;
  }
  await quiet(run.world, id);
  const messages = await messagesOf(run.world, id);
  const index = messages.findIndex(
    (message) => message["text"] === "[from gm] TASK wake up",
  );
  const answered = messages
    .slice(index + 1)
    .some((message) => message["type"] === "assistant");
  return index >= 0 && answered
    ? undefined
    : "the idle PM did not take the queued prompt as a turn";
}

/** A tool error comes back as an error result to the model, and the next call still works. */
export async function toolErrorProblem(run: Run): Promise<string | undefined> {
  const bad = await gmCall(run, "coven_send", {
    to: "beta",
    text: "hello with no prefix",
  });
  if (bad.status !== "error" || !bad.text.includes("must start with one of")) {
    return `a send with no prefix ended ${bad.status}: ${bad.text.slice(0, 200)}`;
  }
  const unknown = await gmCall(run, "coven_pm_launch", {
    code: "Bad Code",
    repo: run.world.alpha,
    brief: "x",
  });
  if (
    unknown.status !== "error" ||
    !unknown.text.includes("is not a project code")
  ) {
    return `a launch with a bad code ended ${unknown.status}: ${unknown.text.slice(0, 200)}`;
  }
  const next = await turn(run.world, run.gm, [["coven_pm_list", {}]]);
  return next[0]?.status === "completed"
    ? undefined
    : `the call after the errors ended ${next[0]?.status ?? "with no call"}`;
}
