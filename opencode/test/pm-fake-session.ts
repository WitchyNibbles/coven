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

/** An in-memory `ctx.session`: no server, no network. `update` replaces the metadata it is given. */
export function fakeSession(initial: readonly SessionLike[]): FakeSession {
  const sessions = new Map(initial.map((item) => [item.id, item]));
  let updates = 0;
  const fake: FakeSession = {
    sessions,
    calls: [],
    prompts: [],
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
      const start = cursor === undefined ? 0 : Number(cursor);
      const data = all.slice(start, start + fake.pageSize);
      const end = start + fake.pageSize;
      const more = fake.endless || end < all.length;
      return Promise.resolve({
        data,
        cursor: more ? { next: String(fake.endless ? 0 : end) } : {},
      });
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
