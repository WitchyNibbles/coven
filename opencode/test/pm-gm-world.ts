import { spawnSync } from "node:child_process";
import { mkdtempSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type {
  PermissionRule,
  SessionCreateInput,
  SessionLike,
} from "../src/context.ts";
import type { PmStatus } from "../src/pm/manage.ts";
import { coven, fakeSession } from "./pm-fake-session.ts";
import type { FakeSession } from "./pm-fake-session.ts";

export interface World extends FakeSession {
  readonly created: SessionCreateInput[];
  readonly interrupts: string[];
  /**
   * Ids the fake treats as archived in OpenCode: `list` hides them unless asked for them. A test
   * puts an id here as an archive made in the UI or over HTTP would; the plugin cannot make one
   * (2.3.2's plugin-side `update` ignores `archived`). `get` shows them with `time.archived` only
   * while `getShowsArchived` is true.
   */
  readonly archived: Set<string>;
  failInterrupt: boolean;
  getShowsArchived: boolean;
}

/** `fakeSession` plus `create`, `interrupt` and `archived` that a GM tool needs. */
export function gmWorld(initial: readonly SessionLike[]): World {
  const fake = fakeSession(initial) as World & { archived: Set<string> };
  const created: SessionCreateInput[] = [];
  const interrupts: string[] = [];
  const archived = new Set<string>();
  const listBase = fake.list.bind(fake);
  const getBase = fake.get.bind(fake);
  Object.assign(fake, {
    created,
    interrupts,
    archived,
    failInterrupt: false,
    getShowsArchived: true,
  });
  fake.get = async (input) => {
    const found = await getBase(input);
    return fake.getShowsArchived && archived.has(input.sessionID)
      ? { ...found, time: { ...found.time, archived: 1 } }
      : found;
  };
  fake.create = (input) => {
    created.push(input);
    const made: SessionLike = {
      id: `new${created.length}`,
      ...(input.location === undefined ? {} : { location: input.location }),
      ...(input.metadata === undefined ? {} : { metadata: input.metadata }),
    };
    fake.sessions.set(made.id, made);
    return Promise.resolve(made);
  };
  fake.interrupt = ({ sessionID }) => {
    fake.calls.push(`interrupt:${sessionID}`);
    if (fake.failInterrupt) {
      return Promise.reject(new Error("interrupt down"));
    }
    interrupts.push(sessionID);
    return Promise.resolve({});
  };
  fake.list = async (input) => {
    const page = await listBase(input);
    const wanted = input.archived;
    return wanted === undefined
      ? page
      : {
          ...page,
          data: page.data.filter((each) => archived.has(each.id) === wanted),
        };
  };
  return fake;
}

export const GM: SessionLike = { id: "gm1", metadata: coven("gm") };

/** One tool call as session `sessionID`. */
export function call(
  tool: {
    execute(
      input: unknown,
      context: {
        sessionID: string;
        signal: AbortSignal;
        progress(update: { status: string }): Promise<void>;
      },
    ): Promise<{ content: string }>;
  },
  sessionID: string,
  input: unknown,
): Promise<string> {
  return tool
    .execute(input, {
      sessionID,
      signal: new AbortController().signal,
      progress: () => Promise.resolve(),
    })
    .then((out) => out.content);
}

export const SECRET: readonly PermissionRule[] = [
  { action: "read", resource: "*.env", effect: "deny" },
  { action: "edit", resource: "/placeholder/config/skills/*", effect: "deny" },
];

/** A status source with nothing busy, nothing waiting, nothing to say. */
export function quietStatus(over: Partial<PmStatus> = {}): PmStatus {
  return {
    watch: () => Promise.resolve({ asks: [], childBusyMs: undefined }),
    lastMessage: () => Promise.resolve(undefined),
    ...over,
  };
}

/** A real `git init` repository in a fresh tmp folder; its realpath. */
export function gitRepo(): string {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "coven-pm-repo-")));
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")),
  );
  const init = spawnSync("git", ["init", "-q", dir], { env });
  if (init.status !== 0) {
    throw new Error(`git init failed: ${String(init.stderr)}`);
  }
  return dir;
}

/** A tmp folder that is not a git checkout. */
export function plainDir(): string {
  return realpathSync(mkdtempSync(join(tmpdir(), "coven-pm-plain-")));
}
