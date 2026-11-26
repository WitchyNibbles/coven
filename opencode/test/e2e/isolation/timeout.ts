/**
 * What OpenCode does to a PM shell's processes when it ends a call early, looked at from the
 * harness side in the host's `/proc`. A PM shell's spawned process is the wrapper's `unshare`;
 * `--kill-child` has to take the namespace's processes with it when OpenCode kills it.
 *
 * Two ways a call ends early are driven: the call's own `timeout`, and an interrupt of the PM's
 * turn (`coven_pm_stop`). On 2.3.2 the timeout kills nothing (the call moves to the background
 * and the command runs on), so only the interrupt is a kill.
 */
import { readdir, readFile } from "node:fs/promises";

import {
  completedProblem,
  gmCall,
  launch,
  pmCall,
  pmId,
  record,
} from "../hierarchy/run.ts";
import type { Run } from "../hierarchy/run.ts";
import { isBusyInfo, infoOf, sleep } from "../hierarchy/sessions.ts";

/** Numbers no other suite sleeps, so a hit is this check's own process. */
const TIMEOUT_SECONDS = "2987";
const INTERRUPT_SECONDS = "2988";
const CALL_TIMEOUT_MS = 2000;
const SEEN_WITHIN_MS = 15_000;
const GONE_WITHIN_MS = 6000;
const POLL_MS = 100;
const BACKGROUNDED = /moved to the background/i;

/** The pids whose command line is exactly `sleep <seconds>`. */
async function sleepers(seconds: string): Promise<number[]> {
  const names = await readdir("/proc");
  const found = await Promise.all(
    names
      .filter((name) => /^\d+$/.test(name))
      .map(async (name) => {
        const line = await readFile(`/proc/${name}/cmdline`, "utf8").catch(
          () => "",
        );
        return line === `sleep\0${seconds}\0` ? Number(name) : undefined;
      }),
  );
  return found.filter((pid): pid is number => pid !== undefined);
}

/** Polls until `done(found)` or `ms` pass; returns the last list. */
async function pollUntil(
  seconds: string,
  ms: number,
  done: (found: number[]) => boolean,
): Promise<number[]> {
  const deadline = Date.now() + ms;
  let found = await sleepers(seconds);
  while (!done(found) && Date.now() < deadline) {
    await sleep(POLL_MS);
    found = await sleepers(seconds);
  }
  return found;
}

/** Ends the processes found, so that a survivor does not outlive the suite. */
function killAll(pids: readonly number[]): void {
  for (const pid of pids) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // gone already
    }
  }
}

/**
 * A PM's shell runs `sleep` with a 2 s timeout. The sleep must be seen while the call runs (so
 * that the search can tell). When OpenCode kills the command at its timeout, nothing may be left;
 * when it moves the call to the background instead (what 2.3.2 does), it sent no kill, the
 * command is meant to run on, and the check ends it and records what happened.
 */
export async function timeoutProblem(
  run: Run,
  workdir: string,
): Promise<string | undefined> {
  const call = pmCall(run, "alpha", "shell", {
    command: `sleep ${TIMEOUT_SECONDS}`,
    workdir,
    timeout: CALL_TIMEOUT_MS,
  });
  let ended = false;
  void call.then(
    () => (ended = true),
    () => (ended = true),
  );
  const during = await pollUntil(
    TIMEOUT_SECONDS,
    SEEN_WITHIN_MS,
    (found) => found.length > 0 || ended,
  );
  const result = await call;
  if (during.length === 0) {
    return `the sleep never showed in /proc during the call, so the search cannot tell (call ${result.status}: ${result.text.slice(0, 160)})`;
  }
  const backgrounded = BACKGROUNDED.test(result.text);
  const after = backgrounded
    ? await sleepers(TIMEOUT_SECONDS)
    : await pollUntil(
        TIMEOUT_SECONDS,
        GONE_WITHIN_MS,
        (found) => found.length === 0,
      );
  record(
    "timeout",
    `${result.status}: ${result.text.slice(0, 90).replaceAll("\n", " ")} | sleeps after: ${after.length}`,
  );
  killAll(after);
  if (backgrounded) {
    return after.length > 0
      ? undefined
      : "OpenCode moved the call to the background but its sleep is gone";
  }
  return after.length === 0
    ? undefined
    : `FAIL: after OpenCode's timeout (${result.text.slice(0, 80)}) ${after.length} process(es) still run "sleep ${TIMEOUT_SECONDS}": ${after.join(", ")}`;
}

/**
 * A PM is busy in a shell `sleep`; the GM stops it (OpenCode interrupts the turn and kills the
 * call's process). The sleep must be gone from the host's `/proc` afterwards.
 */
export async function interruptProblem(
  run: Run,
  repo: string,
): Promise<string | undefined> {
  const launched = await launch(run, "gamma", repo, [
    ["shell", { command: `sleep ${INTERRUPT_SECONDS}` }],
  ]);
  const bad = completedProblem(launched);
  if (bad !== undefined) {
    return bad;
  }
  const seen = await pollUntil(
    INTERRUPT_SECONDS,
    SEEN_WITHIN_MS,
    (found) => found.length > 0,
  );
  if (seen.length === 0) {
    return "the PM's sleep never showed in /proc, so the search cannot tell";
  }
  const id = pmId(run, "gamma");
  if (!isBusyInfo(await infoOf(run.world, id))) {
    killAll(seen);
    return "the PM is not busy while its sleep runs";
  }
  const stopped = await gmCall(run, "coven_pm_stop", { code: "gamma" });
  const problem = completedProblem(stopped);
  const after = await pollUntil(
    INTERRUPT_SECONDS,
    GONE_WITHIN_MS,
    (found) => found.length === 0,
  );
  killAll(after);
  return (
    problem ??
    (after.length === 0
      ? undefined
      : `FAIL: after the interrupt ${after.length} process(es) still run "sleep ${INTERRUPT_SECONDS}": ${after.join(", ")}`)
  );
}
