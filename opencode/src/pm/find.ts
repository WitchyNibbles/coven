import type { SessionApi, SessionLike } from "../context.ts";
import { isPlainObject } from "../join/fields.ts";
import { isRoot } from "./role.ts";

/** How many pages of `list` one lookup reads before it gives up. */
export const MAX_PAGES = 50;

/** The `coven` entry of a session's own metadata; `{}` when it has none. */
function covenOf(session: SessionLike): Record<string, unknown> {
  const coven = session.metadata?.["coven"];
  return isPlainObject(coven) ? coven : {};
}

/**
 * The root sessions, live or archived, page by page. A `list` that throws, or a listing still
 * going after `MAX_PAGES` pages, throws: "nobody found" is never a safe answer from a lookup
 * that did not finish.
 */
export async function* roots(
  session: SessionApi,
  archived = false,
): AsyncGenerator<SessionLike> {
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const listed = await session.list({
      archived,
      ...(cursor === undefined ? {} : { cursor }),
    });
    for (const found of listed.data) {
      if (isRoot(found)) {
        yield found;
      }
    }
    cursor = listed.cursor.next;
    if (cursor === undefined) {
      return;
    }
  }
  throw new Error(`session list has more than ${MAX_PAGES} pages`);
}

/**
 * The GM: the live root session (no `parentID`) whose own metadata holds `role: gm`. A GM's
 * child copies the role and is never the GM.
 */
export async function findGm(
  session: SessionApi,
): Promise<SessionLike | undefined> {
  for await (const found of roots(session)) {
    if (covenOf(found)["role"] === "gm") {
      return found;
    }
  }
  return undefined;
}

/** Every live root session other than `exceptID` that holds `role: gm`: the caller's rivals. */
export async function findOtherGms(
  session: SessionApi,
  exceptID: string,
): Promise<SessionLike[]> {
  const others: SessionLike[] = [];
  for await (const found of roots(session)) {
    if (found.id !== exceptID && covenOf(found)["role"] === "gm") {
      others.push(found);
    }
  }
  return others;
}

/** The PM with this code: a root session whose own metadata holds `role: pm` and the code. */
export async function findPm(
  session: SessionApi,
  code: string,
  archived = false,
): Promise<SessionLike | undefined> {
  for await (const found of roots(session, archived)) {
    const coven = covenOf(found);
    if (coven["role"] === "pm" && coven["code"] === code) {
      return found;
    }
  }
  return undefined;
}

/** Every live PM: its root session and the code its metadata holds. */
export async function listPms(
  session: SessionApi,
): Promise<{ session: SessionLike; code: string }[]> {
  const found: { session: SessionLike; code: string }[] = [];
  for await (const each of roots(session)) {
    const coven = covenOf(each);
    if (coven["role"] === "pm" && typeof coven["code"] === "string") {
      found.push({ session: each, code: coven["code"] });
    }
  }
  return found;
}
