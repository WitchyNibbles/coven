import { ROLE_ENV } from "../src/pm/role.ts";
import type { PmIsolation } from "../src/pm/shell-env.ts";

/** What a PM's shell gets with the isolation found at `/cv`: the wrapper and the folders to hide. */
export const ISOLATION: PmIsolation = {
  wrapper: "/cv/hooks/pm-shell-isolate.sh",
  hide: "/p/state/serve:/p/data",
  found: true,
  problem: undefined,
};

/** The shell the `shellEvent` helpers give an event; the wrapper keeps it in the env. */
export const REAL_SHELL = "/bin/sh";

/** The env a PM's shell ends with, on top of what the scrub leaves. */
export function pmEnv(
  rest: Record<string, string> = {},
): Record<string, string> {
  return {
    ...rest,
    [ROLE_ENV]: "pm",
    COVEN_PM_REAL_SHELL: REAL_SHELL,
    COVEN_PM_HIDE: ISOLATION.hide,
  };
}
