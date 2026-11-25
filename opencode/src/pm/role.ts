import type { SessionLike } from "../context.ts";
import { isPlainObject } from "../join/fields.ts";

/**
 * The variable a PM's shell carries; the key of the `env` dict in `pm_settings`
 * (`src/coven/pm/profile.py`), which a contract test reads.
 */
export const ROLE_ENV = "COVEN_SESSION_ROLE";

export type Role = "gm" | "pm" | "former-gm";

export interface RoleInfo {
  role: Role;
  code?: string;
}

const ROLES: ReadonlySet<unknown> = new Set<Role>(["gm", "pm", "former-gm"]);

function isRole(value: unknown): value is Role {
  return ROLES.has(value);
}
const MAX_HOPS = 32;
/** What a lookup that cannot be trusted resolves to: the strictest role. */
const FAIL_CLOSED: RoleInfo = { role: "pm" };

type Outcome = RoleInfo | "none" | "unreadable";

/** The role a session's own metadata holds; `unreadable` when `coven` is there but not a role. */
function ownRole(session: SessionLike): Outcome {
  const metadata: unknown = session.metadata;
  if (!isPlainObject(metadata) || !("coven" in metadata)) {
    return "none";
  }
  const coven = metadata["coven"];
  if (!isPlainObject(coven)) {
    return "unreadable";
  }
  const role = coven["role"];
  if (!isRole(role)) {
    return "unreadable";
  }
  const code = coven["code"];
  return typeof code === "string" ? { role, code } : { role };
}

/**
 * The role of a session: its own `metadata.coven`, else its nearest parent's. A chain that ends
 * with no role gives `undefined`. Anything that cannot be read (a `get` that throws or finds
 * nothing, a cycle, more than 32 hops, a malformed `coven` entry) gives a PM, so a session that
 * cannot be placed gets the PM guards.
 */
export async function resolveRole(
  sessionID: string,
  get: (id: string) => Promise<SessionLike | undefined>,
): Promise<RoleInfo | undefined> {
  const seen = new Set<string>();
  let next: string | undefined = sessionID;
  while (next !== undefined) {
    if (seen.has(next) || seen.size >= MAX_HOPS) {
      return FAIL_CLOSED;
    }
    seen.add(next);
    let session: SessionLike | undefined;
    try {
      session = await get(next);
    } catch {
      return FAIL_CLOSED;
    }
    if (session === undefined) {
      return FAIL_CLOSED;
    }
    const found = ownRole(session);
    if (found === "unreadable") {
      return FAIL_CLOSED;
    }
    if (found !== "none") {
      return found;
    }
    next = session.parentID ?? undefined;
  }
  return undefined;
}

/** True for a session with no parent (a missing or null `parentID`): a lead session, not a child. */
export function isRoot(session: SessionLike): boolean {
  return session.parentID === undefined || session.parentID === null;
}
