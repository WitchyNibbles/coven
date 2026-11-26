/**
 * A PM's shell whose workdir is a symlink into the serve folder. The tmpfs hides a folder by path,
 * so a shell that starts inside it would keep the real folder under its feet. The server guard
 * resolves a workdir link and refuses it (this is the outcome the check records); a link flipped
 * between the guard's check and the spawn's chdir would put a shell in the same place, which the
 * wrapper's own refusal covers (its tests). The link is made here, outside the PM.
 */
import { readFile, rm, symlink } from "node:fs/promises";
import { join } from "node:path";

import { pmCall, record } from "../hierarchy/run.ts";
import type { Run } from "../hierarchy/run.ts";

const LINK = "isolation-serve-link";
const SAFE_LINK = "isolation-repo-link";
const GUARD_LABEL = "coven opencode-server-guard";
const WRAPPER_REFUSAL = "working directory is inside a hidden folder";

/** How the call ended, for the record line. */
function outcomeOf(call: { status: string; text: string }): string {
  if (call.text.includes(WRAPPER_REFUSAL)) {
    return "refused by the wrapper";
  }
  if (call.status !== "completed" && call.text.includes(GUARD_LABEL)) {
    return "refused by the server guard";
  }
  return `${call.status}, the password is not in the output`;
}

/**
 * The PM's shell in a linked workdir either is refused or does not print the password. The
 * control is a link to the PM's own repo, whose shell completes in the real path (so the call's
 * shape is right).
 */
export async function linkedWorkdirProblem(
  run: Run,
  serve: string,
  repo: string,
): Promise<string | undefined> {
  const password = (await readFile(join(serve, "password"), "utf8")).trim();
  if (password === "") {
    return "the password file is empty, so the check cannot tell";
  }
  const link = join(repo, LINK);
  const safe = join(repo, SAFE_LINK);
  await Promise.all([rm(link, { force: true }), rm(safe, { force: true })]);
  await symlink(serve, link);
  await symlink(repo, safe);
  try {
    const control = await pmCall(run, "alpha", "shell", {
      command: "pwd -P",
      workdir: safe,
    });
    if (control.status !== "completed" || !control.text.includes(repo)) {
      return `the control failed: a PM shell through a link to its repo ended ${control.status}: ${control.text.slice(0, 160)}`;
    }
    const call = await pmCall(run, "alpha", "shell", {
      command: "cat password",
      workdir: link,
    });
    record("link", outcomeOf(call));
    return call.text.includes(password)
      ? "FAIL: a PM shell whose workdir is a link into the serve folder printed the password"
      : undefined;
  } finally {
    await Promise.all([rm(link, { force: true }), rm(safe, { force: true })]);
  }
}
