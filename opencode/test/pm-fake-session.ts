import type {
  SessionApi,
  SessionLike,
  SessionPage,
  SessionPromptInput,
  SessionUpdateInput,
} from "../src/context.ts";

export interface FakeSession extends SessionApi {
  readonly sessions: Map<string, SessionLike>;
  /** Every call, in order: `update:<id>:<role>`, `prompt:<id>`, `list`. */
  readonly calls: string[];
  readonly prompts: SessionPromptInput[];
  /** Every `update` input, in order. */
  readonly updates: SessionUpdateInput[];
  /** Make the n-th `update` call (1-based) throw. */
  failUpdate: number | undefined;
  failList: boolean;
  failPrompt: boolean;
  /** Sessions per `list` page. */
  pageSize: number;
  /** Pages `list` has served. */
  pages: number;
  /** Always hand out a next cursor, as a server that never ends would. */
  endless: boolean;
}

export function coven(role: string, code?: string): Record<string, unknown> {
  return { coven: { role, ...(code === undefined ? {} : { code }) } };
}

/**
 * One page of `list` as OpenCode 2.3.2 serves it: a page with sessions has a string `cursor.next`
 * (even the last one), the page after answers `{previous: null, next: null}`, and a `null` cursor
 * in the query is refused as the SDK refuses it. `endless` hands out a next cursor forever.
 */
export function realPage(
  all: readonly SessionLike[],
  query: { cursor?: string | null | undefined },
  size: number,
  endless = false,
): Promise<SessionPage> {
  if (query.cursor === null) {
    return Promise.reject(
      new Error('Expected SessionsCursor | undefined at ["cursor"]'),
    );
  }
  const start = Number(query.cursor ?? 0);
  const data = all.slice(start, start + size);
  const more = endless || data.length > 0;
  return Promise.resolve({
    data,
    cursor: more
      ? {
          previous: String(start),
          next: String(endless ? 0 : start + size),
        }
      : { previous: null, next: null },
  });
}

/** An in-memory `ctx.session`: no server, no network. `update` replaces the metadata it is given. */
export function fakeSession(initial: readonly SessionLike[]): FakeSession {
  const sessions = new Map(initial.map((item) => [item.id, item]));
  let updates = 0;
  const fake: FakeSession = {
    sessions,
    calls: [],
    prompts: [],
    updates: [],
    failUpdate: undefined,
    failList: false,
    failPrompt: false,
    pageSize: 100,
    pages: 0,
    endless: false,
    create(): Promise<SessionLike> {
      return Promise.reject(new Error("create is not faked"));
    },
    get({ sessionID }): Promise<SessionLike> {
      const found = sessions.get(sessionID);
      return found === undefined
        ? Promise.reject(new Error(`no session ${sessionID}`))
        : Promise.resolve(found);
    },
    list({ cursor, parentID }): Promise<SessionPage> {
      fake.calls.push("list");
      if (fake.failList) {
        return Promise.reject(new Error("list down"));
      }
      fake.pages += 1;
      const all = [...sessions.values()].filter(
        (each) => parentID === undefined || each.parentID === parentID,
      );
      return realPage(all, { cursor }, fake.pageSize, fake.endless);
    },
    prompt(input: SessionPromptInput): Promise<unknown> {
      fake.calls.push(`prompt:${input.sessionID}`);
      if (fake.failPrompt) {
        return Promise.reject(new Error("prompt down"));
      }
      fake.prompts.push(input);
      return Promise.resolve({});
    },
    interrupt(): Promise<unknown> {
      return Promise.resolve({});
    },
    update(input: SessionUpdateInput): Promise<void> {
      updates += 1;
      fake.updates.push(input);
      const role = (input.metadata?.["coven"] as { role?: string } | undefined)
        ?.role;
      fake.calls.push(`update:${input.sessionID}:${role ?? ""}`);
      if (fake.failUpdate === updates) {
        return Promise.reject(new Error("update down"));
      }
      const found = sessions.get(input.sessionID);
      if (found === undefined) {
        return Promise.reject(new Error(`no session ${input.sessionID}`));
      }
      sessions.set(input.sessionID, {
        ...found,
        ...(input.metadata === undefined ? {} : { metadata: input.metadata }),
      });
      return Promise.resolve();
    },
  };
  return fake;
}
