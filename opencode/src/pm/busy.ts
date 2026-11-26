import type { SessionApi, SessionLike } from "../context.ts";

/** How many pages of `list` one lookup reads before it gives up. */
export const MAX_PAGES = 50;

/** How many sessions one walk of a tree reads before it gives up. */
const MAX_TREE = 500;

/**
 * True while the session's turn runs: it turned busy and has not gone idle since. 2.3.2 puts both
 * times on the session (`time.busy`, `time.idle`), so the server itself says who is working.
 * A session that turned busy before `since` is not counted: a restart mid-turn never writes
 * `idle`, and that turn is gone.
 */
export function isBusy(session: SessionLike, since = 0): boolean {
  const busy = session.time?.busy;
  if (busy === undefined || busy < since) {
    return false;
  }
  const idle = session.time?.idle;
  return idle === undefined || idle < busy;
}

/** The ids of the sessions whose turn runs. */
export function busyIds(sessions: readonly SessionLike[]): Set<string> {
  return new Set(
    sessions.filter((session) => isBusy(session)).map((session) => session.id),
  );
}

interface ListQuery {
  archived?: boolean;
  parentID?: string;
}

/**
 * The sessions `list` returns for the query, one by one, page by page; a page is read only when
 * the one before it is used up. The walk ends on a page with no `cursor.next`: 2.3.2 answers
 * `null` there (on the page after the last), older shapes leave it out, and a `null` is never
 * sent back as a `cursor`, which the SDK refuses. A `list` that throws, or a listing still going
 * after `MAX_PAGES` pages, throws: a listing not read to the end says nothing, and "nobody found"
 * is never a safe answer from a lookup that did not finish.
 */
export async function* pages(
  session: SessionApi,
  query: ListQuery,
): AsyncGenerator<SessionLike, void> {
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const listed = await session.list({
      ...query,
      ...(cursor === undefined ? {} : { cursor }),
    });
    yield* listed.data;
    cursor = listed.cursor.next ?? undefined;
    if (cursor === undefined) {
      return;
    }
  }
  throw new Error(`session list has more than ${MAX_PAGES} pages`);
}

/** Every session `pages` gives for the query. */
export async function listAll(
  session: SessionApi,
  query: ListQuery,
): Promise<SessionLike[]> {
  const found: SessionLike[] = [];
  for await (const each of pages(session, query)) {
    found.push(each);
  }
  return found;
}

/**
 * The sessions below `rootID`, level by level: children, their children and so on, each level
 * read only when the one before it is used up. Throws like `pages`, and for a tree past
 * `MAX_TREE` sessions: a tree not read to the end is not "no children".
 */
export async function* walkTree(
  session: SessionApi,
  rootID: string,
): AsyncGenerator<SessionLike, void> {
  const queue = [rootID];
  let seen = 0;
  for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
    for await (const child of pages(session, { parentID: next })) {
      seen += 1;
      queue.push(child.id);
      yield child;
    }
    if (seen > MAX_TREE) {
      throw new Error(`session tree has more than ${MAX_TREE} sessions`);
    }
  }
}

/** Every session `walkTree` gives below `rootID`. */
export async function descendants(
  session: SessionApi,
  rootID: string,
): Promise<SessionLike[]> {
  const found: SessionLike[] = [];
  for await (const child of walkTree(session, rootID)) {
    found.push(child);
  }
  return found;
}
