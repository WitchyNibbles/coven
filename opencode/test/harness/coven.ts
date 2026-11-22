import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

import { exec } from "./process.ts";
import type { Finished } from "./process.ts";

/** The repository's own launcher; the harness never uses a coven from PATH. */
const COVEN_BIN = resolve(
  fileURLToPath(new URL("../../../bin/coven", import.meta.url)),
);

/** The command and arguments that run `bin/coven` with `args`. */
export function covenCommand(args: readonly string[]): {
  command: string;
  args: string[];
} {
  return { command: "sh", args: [COVEN_BIN, ...args] };
}

/** Runs `sh <repo>/bin/coven <args>` to its end. */
export function coven(
  args: string[],
  options: { cwd: string; env: Record<string, string>; timeoutMs: number },
): Promise<Finished> {
  const run = covenCommand(args);
  return exec(run.command, run.args, options);
}
