import type { SessionApi, SessionLike } from "../context.ts";
import { isPlainObject } from "../join/fields.ts";
import { pages } from "./busy.ts";
import { isRoot } from "./role.ts";

/** The `coven` entry of a session's own metadata; `{}` when it has none. */
export function covenOf(session: SessionLike): Record<string, unknown> {
  const coven = session.metadata?.["coven"];
  return isPlainObject(coven) ? coven : {};
}

export interface PmRoot {
  session: SessionLike;
  code: string;
}

/** The code a session's own metadata holds when it says `role: pm`; else `undefined`. */
function pmCodeOf(session: SessionLike): string | undefined {
  const coven = covenOf(session);
  const code = coven["code"];
  return coven["role"] === "pm" && typeof code === "string" ? code : undefined;
}

/**
 * The root sessions (no `parentID`), live or archived, that `match` accepts, in listing order and
 * page by page: the next page is read only when the caller asks for one more. A `list` that
 * throws, or a listing still going after `MAX_PAGES` pages, throws.
 */
async function* rootsWhere(
  session: SessionApi,
  match: (root: SessionLike) => boolean,
  archived = false,
): AsyncGenerator<SessionLike, undefined> {
  for await (const found of pages(session, { archived })) {
    if (isRoot(found) && match(found)) {
      yield found;
    }
  }
  return undefined;
}

const holdsGm = (session: SessionLike): boolean =>
  covenOf(session)["role"] === "gm";

/**
 * The GM: the live root session (no `parentID`) whose own metadata holds `role: gm`. A GM's
 * child copies the role and is never the GM.
 */
export async function findGm(
  session: SessionApi,
): Promise<SessionLike | undefined> {
  return (await rootsWhere(session, holdsGm).next()).value;
}

/** Every live root session other than `exceptID` that holds `role: gm`: the caller's rivals. */
export async function findOtherGms(
  session: SessionApi,
  exceptID: string,
): Promise<SessionLike[]> {
  const others: SessionLike[] = [];
  const rivals = rootsWhere(
    session,
    (root) => root.id !== exceptID && holdsGm(root),
  );
  for await (const found of rivals) {
    others.push(found);
  }
  return others;
}

/** The PM with this code: a root session whose own metadata holds `role: pm` and the code. */
export async function findPm(
  session: SessionApi,
  code: string,
  archived = false,
): Promise<SessionLike | undefined> {
  const holdsCode = (root: SessionLike): boolean => pmCodeOf(root) === code;
  return (await rootsWhere(session, holdsCode, archived).next()).value;
}

/** The PMs among sessions already listed: each root session whose metadata holds `role: pm` and a code. */
export function pmRootsIn(sessions: readonly SessionLike[]): PmRoot[] {
  return sessions.flatMap((each) => {
    const code = pmCodeOf(each);
    return isRoot(each) && code !== undefined ? [{ session: each, code }] : [];
  });
}
