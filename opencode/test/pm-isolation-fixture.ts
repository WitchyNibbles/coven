import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ROLE_ENV } from "../src/pm/role.ts";
import type { PmIsolation } from "../src/pm/shell-env.ts";

/** The folder the wrapper's pid records go under: a real one, since a PM's shell makes a folder in it. */
export const PIDS_DIR = mkdtempSync(join(tmpdir(), "coven-pids-"));
process.on("exit", () => rmSync(PIDS_DIR, { recursive: true, force: true }));

/** What a PM's shell gets with the isolation found at `/cv`: the wrapper and the folders to hide. */
export const ISOLATION: PmIsolation = {
  wrapper: "/cv/hooks/pm-shell-isolate.sh",
  hide: "/p/state/serve:/p/data",
  pidsDir: PIDS_DIR,
  found: true,
  problem: undefined,
};

/** The shell the `shellEvent` helpers give an event; the wrapper keeps it in the env. */
export const REAL_SHELL = "/bin/sh";

/** The PM session the tests' PM shells belong to. */
export const PM_SESSION_ID = "ses_pm";

/** The env a PM's shell ends with, on top of what the scrub leaves. */
export function pmEnv(
  rest: Record<string, string> = {},
): Record<string, string> {
  return {
    ...rest,
    [ROLE_ENV]: "pm",
    COVEN_PM_REAL_SHELL: REAL_SHELL,
    COVEN_PM_HIDE: ISOLATION.hide,
    COVEN_PM_SESSION: PM_SESSION_ID,
    COVEN_PM_PIDS: join(PIDS_DIR, PM_SESSION_ID),
  };
}
