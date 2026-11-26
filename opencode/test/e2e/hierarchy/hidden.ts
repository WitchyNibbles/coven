/**
 * The hidden flag of `coven_pm_stop {archive:true}`: 2.3.2's plugin-side `session.update` ignores
 * `archived`, so the stop sets `coven.archived` in the session's metadata instead. These checks
 * drive the real GM tools; the plugin's own listing code against a PM archived over HTTP is the
 * pm-api suite's job.
 */
import { at } from "./api.ts";
import { completedProblem, gmCall, pmId } from "./run.ts";
import type { Run } from "./run.ts";
import { infoOf, messagesOf, quiet } from "./sessions.ts";
import { listLine } from "./manage.ts";

/** The stop's text, then what it says of the isolation trees it swept (none run here). */
const STOPPED =
  /^stopped pm-delta; hidden from coven_pm_list; the session is not archived in OpenCode; killed 0 isolation tree\(s\)$/;

/** The ids the server lists as archived. */
async function archivedIds(run: Run): Promise<string[]> {
  const answer = await run.world.api.get("/api/session?archived=true");
  const data = (at(answer.body, "data") as { id: string }[] | undefined) ?? [];
  return data.map((item) => item.id);
}

/** The line of `coven_pm_list archived:true` for `code`. */
async function hiddenLine(run: Run, code: string): Promise<string | undefined> {
  const list = await gmCall(run, "coven_pm_list", { archived: true });
  if (list.status !== "completed") {
    throw new Error(
      `coven_pm_list archived true ended ${list.status}: ${list.text.slice(0, 200)}`,
    );
  }
  return list.text.split("\n").find((line) => line.startsWith(`pm-${code} `));
}

/** What is wrong with the stop's answer and with where the PM went, or undefined. */
async function stopProblem(
  run: Run,
  id: string,
  said: string,
): Promise<string | undefined> {
  const coven = at(await infoOf(run.world, id), "metadata", "coven");
  const shown = await listLine(run, "delta");
  const all = await hiddenLine(run, "delta");
  const checks: [boolean, string][] = [
    [STOPPED.test(said), `the stop says: ${said}`],
    [
      at(coven, "archived") === true &&
        at(coven, "role") === "pm" &&
        at(coven, "code") === "delta",
      `after the stop the coven metadata is ${JSON.stringify(coven)}`,
    ],
    [
      shown === undefined,
      `the default list shows a hidden PM: ${String(shown)}`,
    ],
    [
      /^pm-delta {2}\S+ {2}archived {2}/.test(all ?? ""),
      `the list with archived true shows: ${String(all)}`,
    ],
    [
      !(await archivedIds(run)).includes(id),
      "the session is archived in OpenCode, which the stop must not do",
    ],
  ];
  return checks.find(([ok]) => !ok)?.[1];
}

/** `coven_pm_stop` with `archive: true` hides the PM by the flag and leaves the session unarchived in OpenCode. */
export async function archiveStopProblem(
  run: Run,
): Promise<string | undefined> {
  const stopped = await gmCall(run, "coven_pm_stop", {
    code: "delta",
    archive: true,
  });
  return (
    completedProblem(stopped) ??
    (await stopProblem(run, pmId(run, "delta"), stopped.text))
  );
}

/** `coven_pm_resume` of a hidden PM clears the flag, lists it again and queues the RESUMED turn. */
export async function archiveResumeProblem(
  run: Run,
): Promise<string | undefined> {
  const id = pmId(run, "delta");
  const resumed = await gmCall(run, "coven_pm_resume", {
    code: "delta",
    text: "pick up where you stopped",
  });
  const bad = completedProblem(resumed);
  if (bad !== undefined) {
    return bad;
  }
  await quiet(run.world, id);
  const messages = await messagesOf(run.world, id);
  const seen = messages.some(
    (message) => message["text"] === "RESUMED by gm\npick up where you stopped",
  );
  const coven = at(await infoOf(run.world, id), "metadata", "coven");
  const line = await listLine(run, "delta");
  const checks: [boolean, string][] = [
    [seen, "the resumed PM never received the RESUMED text"],
    [
      at(coven, "archived") === undefined,
      `the flag is still set: ${JSON.stringify(coven)}`,
    ],
    [
      at(coven, "role") === "pm" && at(coven, "code") === "delta",
      `the PM's own metadata changed: ${JSON.stringify(coven)}`,
    ],
    [line !== undefined, "the resumed PM is not in the default list"],
  ];
  return checks.find(([ok]) => !ok)?.[1];
}
