import type { EvaluateEvent } from "../context.ts";
import type { RoleInfo, RoleLookup } from "./role.ts";

/** The role of a session, as `resolveRole` gives it with the plugin's `get` bound in. */
export type RoleResolver = (sessionID: string) => Promise<RoleInfo | undefined>;

const MESSAGE =
  "a PM may not wait on a prompt: send it to the GM as a QUESTION or PROD-STEP";

/**
 * True when the session is in a PM tree. A lookup that throws counts as a PM: a session that
 * cannot be placed gets the strictest guards.
 */
export async function inPmTree(role: RoleLookup): Promise<boolean> {
  try {
    return (await role())?.role === "pm";
  } catch {
    return true;
  }
}

/**
 * The `permission.hook("evaluate")` handler: an `ask` in a PM tree becomes a `deny`, since a PM
 * has nobody to answer a prompt. The role is looked up only for an `ask`.
 */
export function askToDeny(
  resolve: RoleResolver,
): (event: EvaluateEvent) => Promise<void> {
  return async (event) => {
    if (
      event.effect === "ask" &&
      (await inPmTree(() => resolve(event.sessionID)))
    ) {
      event.effect = "deny";
      event.message = MESSAGE;
    }
  };
}
