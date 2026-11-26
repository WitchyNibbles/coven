/**
 * A PM's Write and Edit tool calls into the serve folder. Those tools run in the server process,
 * outside the PM shells' namespace, so the namespace cannot hold them: the server guard must refuse
 * them, and the wrapper copy and the password must stay as they were.
 */
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

import { pmCall, refusalProblem } from "../hierarchy/run.ts";
import type { Run } from "../hierarchy/run.ts";

const LABEL = "opencode-server-guard";
const WRAPPER_NAME = "pm-shell-isolate.sh";
const PLANTED = "planted.txt";
const CONTROL = "isolation-control.txt";

/** The sha256 of a file, or `missing` when it cannot be read. */
async function digest(path: string): Promise<string> {
  const content = await readFile(path).catch(() => undefined);
  return content === undefined
    ? "missing"
    : createHash("sha256").update(content).digest("hex");
}

/** What the serve folder holds now: the names, and the digests of the two files a PM would aim at. */
async function snapshot(serve: string): Promise<string> {
  const wrapper = join(serve, "isolate", WRAPPER_NAME);
  const names = (await readdir(serve)).sort().join(",");
  return `${names} | ${await digest(wrapper)} | ${await digest(join(serve, "password"))}`;
}

/** The calls a PM must not get through, with what each one is. */
function attempts(
  serve: string,
): [what: string, tool: string, args: Record<string, unknown>][] {
  const wrapper = join(serve, "isolate", WRAPPER_NAME);
  const password = join(serve, "password");
  return [
    [
      "write over the wrapper copy",
      "write",
      { path: wrapper, content: '#!/bin/sh\nexec "$@"\n' },
    ],
    [
      "write over the password",
      "write",
      { path: password, content: "guess\n" },
    ],
    [
      "write a new file in the serve folder",
      "write",
      { path: join(serve, PLANTED), content: "planted\n" },
    ],
    [
      "edit the wrapper copy",
      "edit",
      { path: wrapper, oldString: "unshare", newString: "true" },
    ],
    [
      "edit the password",
      "edit",
      { path: password, oldString: "a", newString: "b" },
    ],
  ];
}

/**
 * Every attempt is an error naming the server guard, the serve folder is as it was, and a Write
 * and an Edit of a file in the PM's repo (the control: the calls' shape is right) both complete.
 */
export async function serveWriteProblem(
  run: Run,
  serve: string,
  repo: string,
): Promise<string | undefined> {
  const before = await snapshot(serve);
  for (const [what, tool, args] of attempts(serve)) {
    const problem = refusalProblem(
      await pmCall(run, "alpha", tool, args),
      LABEL,
    );
    if (problem !== undefined) {
      return `${what}: ${problem}`;
    }
  }
  const after = await snapshot(serve);
  if (after !== before) {
    return `the serve folder changed: ${before} became ${after}`;
  }
  const control = join(repo, CONTROL);
  const made = await pmCall(run, "alpha", "write", {
    path: control,
    content: "one\n",
  });
  const edited = await pmCall(run, "alpha", "edit", {
    path: control,
    oldString: "one",
    newString: "two",
  });
  const done = (call: typeof made): boolean => call.status === "completed";
  if (!done(made) || !done(edited)) {
    return `the control Write and Edit in the repo ended ${made.status} and ${edited.status}: ${made.text.slice(0, 120)} / ${edited.text.slice(0, 120)}`;
  }
  return (await readFile(control, "utf8")) === "two\n"
    ? undefined
    : "the control Edit did not change the file";
}
