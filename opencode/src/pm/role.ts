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

/**
 * The role of the session a tool call belongs to. One call makes one lookup and hands this to
 * every hook that needs the role, so they all see the same answer and the server is asked once.
 */
export type RoleLookup = () => Promise<RoleInfo | undefined>;

/** The session that holds the role of the session a tool call belongs to, from the same lookup. */
export type HolderLookup = () => Promise<string | undefined>;

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
  return (await resolvePlaced(sessionID, get)).info;
}

/** A role and the session that holds it (a PM tree's root, for a PM's child). */
export interface Placed {
  info: RoleInfo | undefined;
  /** The id of the session whose own metadata holds the role; `undefined` with no role or a failed lookup. */
  holder: string | undefined;
}

const UNPLACED: Placed = { info: FAIL_CLOSED, holder: undefined };

/**
 * `resolveRole` and where the role was found: the same walk up the parents, which also says which
 * session holds it. A PM's shell files its isolation tree under that session, the one
 * `coven_pm_stop` is given. A failed lookup is a PM that is placed nowhere.
 */
export async function resolvePlaced(
  sessionID: string,
  get: (id: string) => Promise<SessionLike | undefined>,
): Promise<Placed> {
  const seen = new Set<string>();
  let next: string | undefined = sessionID;
  while (next !== undefined) {
    if (seen.has(next) || seen.size >= MAX_HOPS) {
      return UNPLACED;
    }
    seen.add(next);
    let session: SessionLike | undefined;
    try {
      session = await get(next);
    } catch {
      return UNPLACED;
    }
    if (session === undefined) {
      return UNPLACED;
    }
    const found = ownRole(session);
    if (found === "unreadable") {
      return UNPLACED;
    }
    if (found !== "none") {
      return { info: found, holder: next };
    }
    next = session.parentID ?? undefined;
  }
  return { info: undefined, holder: undefined };
}

/** True for a session with no parent (a missing or null `parentID`): a lead session, not a child. */
export function isRoot(session: SessionLike): boolean {
  return session.parentID === undefined || session.parentID === null;
}
