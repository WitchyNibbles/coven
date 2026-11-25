/**
 * A plugin the pm-api e2e adds to the scratch profile. On load it makes a GM session and a PM
 * session on the real server, then runs the coven plugin's own code (the profile holds a copy of
 * it, next to this folder) against the real `ctx.session`, and writes one JSON line each to the
 * file named by the `out` option: what a full listing returns (item A), what `ctx.session.update`
 * does with `archived` and what `coven_pm_stop` with archive says (item B), and what
 * `coven_pm_list` shows for a PM whose session was deleted after the list was read (item C).
 * It writes ids, counts and tool messages only.
 */
import { appendFileSync } from "node:fs";

interface Page {
  data: { id: string }[];
  cursor: { next?: string | null };
}

interface Session {
  create(input: {
    title: string;
    metadata: Record<string, unknown>;
  }): Promise<{ id: string }>;
  get(input: { sessionID: string }): Promise<{
    time?: { archived?: number };
  }>;
  list(input: Record<string, unknown>): Promise<Page>;
  update(input: Record<string, unknown>): Promise<unknown>;
  remove(input: { sessionID: string }): Promise<unknown>;
}

interface Tool {
  execute(
    input: unknown,
    context: {
      sessionID: string;
      signal: AbortSignal;
      progress(update: { status: string }): Promise<void>;
    },
  ): Promise<{ content: string }>;
}

interface Plugin {
  busy: { listAll(session: Session, query: object): Promise<{ id: string }[]> };
  manage: {
    pmStopTool(deps: object): Tool;
    pmListTool(deps: object): Tool;
  };
  role: { resolveRole: unknown };
}

type Write = (line: Record<string, unknown>) => void;

async function loadPlugin(): Promise<Plugin> {
  const at = (name: string): string =>
    new URL(`../coven/pm/${name}.ts`, import.meta.url).href;
  return {
    busy: (await import(at("busy"))) as Plugin["busy"],
    manage: (await import(at("manage"))) as Plugin["manage"],
    role: (await import(at("role"))) as Plugin["role"],
  };
}

/** Runs one tool as `sessionID`; its content, or the error it threw. */
async function runTool(
  tool: Tool,
  sessionID: string,
  input: unknown,
): Promise<{ content?: string; error?: string }> {
  try {
    const context = {
      sessionID,
      signal: new AbortController().signal,
      progress: () => Promise.resolve(),
    };
    return { content: (await tool.execute(input, context)).content };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}

async function probe(session: Session, record: Write): Promise<void> {
  const plugin = await loadPlugin();
  const gm = await session.create({
    title: "gm",
    metadata: { coven: { role: "gm" } },
  });
  const pm = await session.create({
    title: "pm-x",
    metadata: { coven: { role: "pm", code: "x" } },
  });
  // item A: a full listing ends, on 2.3.2's null cursor after the last page
  try {
    const all = await plugin.busy.listAll(session, { archived: false });
    const ids = all.map((each) => each.id);
    record({
      listed: ids.length,
      holdsBoth: ids.includes(gm.id) && ids.includes(pm.id),
    });
  } catch (error) {
    record({ listedError: String(error).slice(0, 200) });
  }
  // item B: update leaves `archived` alone; the tool says so instead of "archived it"
  const deps = { session, resolve: plugin.role.resolveRole };
  const updated = await session.update({ sessionID: pm.id, archived: true });
  const afterUpdate = await session.get({ sessionID: pm.id });
  record({
    updateAnswered: String(updated),
    archivedAfterUpdate: afterUpdate.time?.archived !== undefined,
  });
  const stopped = await runTool(plugin.manage.pmStopTool(deps), gm.id, {
    code: "x",
    archive: true,
  });
  record({ stopArchive: stopped });
  // item C: the PM is listed, then its session goes; the list's get says it is gone
  const stale = await session.list({ archived: false });
  await session.remove({ sessionID: pm.id });
  const staleDeps = {
    ...deps,
    session: {
      ...session,
      // the one page read before the delete, and the end of the listing
      list: () => Promise.resolve({ data: stale.data, cursor: { next: null } }),
    },
    status: {
      watch: () => Promise.resolve({ asks: [], childBusyMs: undefined }),
      lastMessage: () => Promise.resolve(undefined),
    },
  };
  const shown = await runTool(plugin.manage.pmListTool(staleDeps), gm.id, {});
  record({ listAfterRemove: shown });
}

export default {
  id: "pm-api-probe",
  setup: (ctx: { options?: { out?: unknown }; session: Session }): void => {
    const out = ctx.options?.out;
    if (typeof out !== "string") {
      throw new Error("pm-api probe: the out option is missing");
    }
    const record: Write = (line) => {
      appendFileSync(out, `${JSON.stringify(line)}\n`);
    };
    // not awaited: a plugin's setup returns before the server answers
    probe(ctx.session, record).then(
      () => {
        record({ done: true });
      },
      (error: unknown) => {
        record({ probeError: String(error).slice(0, 300) });
      },
    );
  },
};
