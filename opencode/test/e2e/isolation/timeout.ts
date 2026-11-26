/**
 * What OpenCode does to a PM shell's processes when it ends a call early, looked at from the
 * harness side in the host's `/proc`. A PM shell's spawned process is the wrapper's `unshare`;
 * `--kill-child` has to take the namespace's processes with it when OpenCode kills it.
 *
 * A call's processes are found by their PID namespace: the call's `sleep` (a number no other
 * suite uses) names the namespace by its `/proc/<pid>/ns/pid` link, and every process with that
 * link (the namespace's init, the wrapper's `sh`, the `sleep`) is the call's tree. The harness
 * only ever kills processes of that namespace.
 *
 * Three ways a call ends are driven: the call's own `timeout`, the stop of a busy PM
 * (`coven_pm_stop`), and the stop of a PM whose call the timeout moved to the background. On 2.3.2
 * the timeout kills nothing (the call moves to the background and the command runs on).
 */
import { readdir, readFile, readlink } from "node:fs/promises";

import {
  completedProblem,
  gmCall,
  launch,
  pmCall,
  pmId,
  record,
  settlePm,
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

/** The PID namespace of a process, e.g. `pid:[4026532801]`; undefined once it is gone. */
function pidNamespace(pid: number | string): Promise<string | undefined> {
  return readlink(`/proc/${pid}/ns/pid`).catch(() => undefined);
}

/** The numbered entries of `/proc`. */
async function pids(): Promise<string[]> {
  return (await readdir("/proc")).filter((name) => /^\d+$/.test(name));
}

/** The pids whose command line is exactly `sleep <seconds>`. */
async function sleepers(seconds: string): Promise<number[]> {
  const found = await Promise.all(
    (await pids()).map(async (name) => {
      const line = await readFile(`/proc/${name}/cmdline`, "utf8").catch(
        () => "",
      );
      return line === `sleep\0${seconds}\0` ? Number(name) : undefined;
    }),
  );
  return found.filter((pid): pid is number => pid !== undefined);
}

/** Every process in the PID namespace `ns`: its init, the wrapper's shell, the command. */
async function inNamespace(ns: string): Promise<number[]> {
  const found = await Promise.all(
    (await pids()).map(async (name) =>
      (await pidNamespace(name)) === ns ? Number(name) : undefined,
    ),
  );
  return found.filter((pid): pid is number => pid !== undefined);
}

/** Polls until `done(found)` or `ms` pass; returns the last list. */
async function pollUntil(
  ms: number,
  find: () => Promise<number[]>,
  done: (found: number[]) => boolean,
): Promise<number[]> {
  const deadline = Date.now() + ms;
  let found = await find();
  while (!done(found) && Date.now() < deadline) {
    await sleep(POLL_MS);
    found = await find();
  }
  return found;
}

/** Ends every process of the namespace, so that a survivor does not outlive the suite. */
async function killNamespace(ns: string | undefined): Promise<void> {
  if (ns === undefined) {
    return;
  }
  for (const pid of await inNamespace(ns)) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // gone already
    }
  }
}

/** The namespace of the call's `sleep` once it shows; undefined when it never does. */
async function namespaceOfSleep(
  seconds: string,
  stop: () => boolean = () => false,
): Promise<string | undefined> {
  const [first] = await pollUntil(
    SEEN_WITHIN_MS,
    () => sleepers(seconds),
    (found) => found.length > 0 || stop(),
  );
  return first === undefined ? undefined : pidNamespace(first);
}

/** The text of what is wrong when the namespace is the harness's own. */
async function notSeparate(ns: string): Promise<string | undefined> {
  const own = await pidNamespace("self");
  return ns === own
    ? `the PM's sleep runs in the harness's own PID namespace (${ns}), so the shell is not isolated`
    : undefined;
}

/** What is left of the namespace after `GONE_WITHIN_MS`, as a sentence; undefined when nothing. */
async function leftOver(ns: string): Promise<string | undefined> {
  const left = await pollUntil(
    GONE_WITHIN_MS,
    () => inNamespace(ns),
    (found) => found.length === 0,
  );
  return left.length === 0
    ? undefined
    : `${left.length} process(es) of ${ns} still run: ${left.join(", ")}`;
}

/** A PM that has made its first turn and is idle, launched into `repo` with an empty brief. */
async function idlePm(
  run: Run,
  code: string,
  repo: string,
): Promise<string | undefined> {
  const bad = completedProblem(await launch(run, code, repo, []));
  if (bad !== undefined) {
    return bad;
  }
  await settlePm(run, code);
  return undefined;
}

/**
 * A PM's shell runs `sleep` with a 2 s timeout. When OpenCode kills the command at its timeout,
 * nothing of the call's namespace may be left. When it moves the call to the background instead
 * (what 2.3.2 does), the sleep must still sit in the PM's own PID namespace (not the harness's),
 * and `coven_pm_stop` of that PM must kill it: the namespace is then gone. Whatever happens, the
 * namespace is killed at the end.
 */
export async function timeoutProblem(
  run: Run,
  repo: string,
): Promise<string | undefined> {
  const made = await idlePm(run, "delta", repo);
  if (made !== undefined) {
    return made;
  }
  const call = pmCall(run, "delta", "shell", {
    command: `sleep ${TIMEOUT_SECONDS}`,
    workdir: repo,
    timeout: CALL_TIMEOUT_MS,
  });
  let ended = false;
  void call.then(
    () => (ended = true),
    () => (ended = true),
  );
  let ns: string | undefined;
  try {
    ns = await namespaceOfSleep(TIMEOUT_SECONDS, () => ended);
    const result = await call;
    if (ns === undefined) {
      return `the sleep never showed in /proc during the call, so the search cannot tell (call ${result.status}: ${result.text.slice(0, 160)})`;
    }
    const outside = await notSeparate(ns);
    if (outside !== undefined) {
      return outside;
    }
    return BACKGROUNDED.test(result.text)
      ? await stopBackgrounded(run, ns, result.text)
      : await killedAtTimeout(ns, result.text);
  } finally {
    await killNamespace(ns);
    await call.catch(() => undefined);
  }
}

/** The call was killed at its timeout: nothing of the namespace may be left. */
async function killedAtTimeout(
  ns: string,
  said: string,
): Promise<string | undefined> {
  const left = await leftOver(ns);
  record("timeout", `killed: ${said.slice(0, 90).replaceAll("\n", " ")}`);
  return left === undefined
    ? undefined
    : `FAIL: after OpenCode's timeout (${said.slice(0, 80)}) ${left}`;
}

/**
 * The call moved to the background: its tree is still there (OpenCode sent no kill), in the PM's
 * namespace; then the stop of the PM must end it.
 */
async function stopBackgrounded(
  run: Run,
  ns: string,
  said: string,
): Promise<string | undefined> {
  const still = await inNamespace(ns);
  record(
    "timeout",
    `${said.slice(0, 90).replaceAll("\n", " ")} | tree after: ${still.length} process(es) in ${ns}`,
  );
  if (still.length === 0) {
    return "OpenCode moved the call to the background but its namespace is gone";
  }
  const stopped = completedProblem(
    await gmCall(run, "coven_pm_stop", { code: "delta" }),
  );
  const left = await leftOver(ns);
  record(
    "timeout stop",
    left === undefined
      ? "coven_pm_stop ended the backgrounded sleep"
      : `coven_pm_stop left the backgrounded sleep: ${left}`,
  );
  return (
    stopped ??
    (left === undefined
      ? undefined
      : `FAIL: coven_pm_stop of the PM left its backgrounded call: ${left}`)
  );
}

/**
 * A PM is busy in a shell `sleep`; the GM stops it (OpenCode interrupts the turn and kills the
 * call's process). Nothing of the call's namespace may be left afterwards.
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
  let ns: string | undefined;
  try {
    ns = await namespaceOfSleep(INTERRUPT_SECONDS);
    if (ns === undefined) {
      return "the PM's sleep never showed in /proc, so the search cannot tell";
    }
    const outside = await notSeparate(ns);
    if (outside !== undefined) {
      return outside;
    }
    if (!isBusyInfo(await infoOf(run.world, pmId(run, "gamma")))) {
      return "the PM is not busy while its sleep runs";
    }
    const problem = completedProblem(
      await gmCall(run, "coven_pm_stop", { code: "gamma" }),
    );
    const left = await leftOver(ns);
    return (
      problem ??
      (left === undefined ? undefined : `FAIL: after the interrupt ${left}`)
    );
  } finally {
    await killNamespace(ns);
  }
}
