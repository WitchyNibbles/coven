import type { SessionApi, SessionLike } from "../context.ts";
import { MAX_PAGES } from "./find.ts";

/** How many sessions one walk of a tree reads before it gives up. */
const MAX_TREE = 500;

/**
 * True while the session's turn runs: it turned busy and has not gone idle since. 2.3.2 puts both
 * times on the session (`time.busy`, `time.idle`), so the server itself says who is working.
 */
export function isBusy(session: SessionLike): boolean {
  const busy = session.time?.busy;
  if (busy === undefined) {
    return false;
  }
  const idle = session.time?.idle;
  return idle === undefined || idle < busy;
}

/**
 * Every session `list` returns for the query, page by page. A `list` that throws, or a listing
 * still going after `MAX_PAGES` pages, throws: a listing not read to the end says nothing.
 */
export async function listAll(
  session: SessionApi,
  query: { archived?: boolean; parentID?: string },
): Promise<SessionLike[]> {
  const found: SessionLike[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const listed = await session.list({
      ...query,
      ...(cursor === undefined ? {} : { cursor }),
    });
    found.push(...listed.data);
    cursor = listed.cursor.next;
    if (cursor === undefined) {
      return found;
    }
  }
  throw new Error(`session list has more than ${MAX_PAGES} pages`);
}

/**
 * Every session below `rootID`: children, their children and so on. Throws like `listAll`, and
 * for a tree past `MAX_TREE` sessions: a tree not read to the end is not "no children".
 */
export async function descendants(
  session: SessionApi,
  rootID: string,
): Promise<SessionLike[]> {
  const found: SessionLike[] = [];
  const queue = [rootID];
  for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
    for (const child of await listAll(session, { parentID: next })) {
      found.push(child);
      queue.push(child.id);
    }
    if (found.length > MAX_TREE) {
      throw new Error(`session tree has more than ${MAX_TREE} sessions`);
    }
  }
  return found;
}
