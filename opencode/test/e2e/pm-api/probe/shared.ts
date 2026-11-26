/** What the pm-api probe's two flows share: the session API's shape, the plugin's code, a tool run. */

export interface Page {
  data: { id: string }[];
  cursor: { next?: string | null };
}

export interface Got {
  metadata?: { coven?: Record<string, unknown> } & Record<string, unknown>;
  time?: Record<string, unknown>;
}

export interface Session {
  create(input: {
    title: string;
    metadata: Record<string, unknown>;
  }): Promise<{ id: string }>;
  get(input: { sessionID: string }): Promise<Got>;
  list(input: Record<string, unknown>): Promise<Page>;
  update(input: Record<string, unknown>): Promise<unknown>;
  remove(input: { sessionID: string }): Promise<unknown>;
}

export interface Tool {
  execute(
    input: unknown,
    context: {
      sessionID: string;
      signal: AbortSignal;
      progress(update: { status: string }): Promise<void>;
    },
  ): Promise<{ content: string }>;
}

/** The coven plugin's own code, as the scratch profile holds it. */
export interface Plugin {
  busy: { listAll(session: Session, query: object): Promise<{ id: string }[]> };
  manage: {
    pmStopTool(deps: object): Tool;
    pmListTool(deps: object): Tool;
    pmResumeTool(deps: object): Tool;
  };
  role: { resolveRole: unknown };
}

export type Write = (line: Record<string, unknown>) => void;

export async function loadPlugin(): Promise<Plugin> {
  const at = (name: string): string =>
    new URL(`../coven/pm/${name}.ts`, import.meta.url).href;
  return {
    busy: (await import(at("busy"))) as Plugin["busy"],
    manage: (await import(at("manage"))) as Plugin["manage"],
    role: (await import(at("role"))) as Plugin["role"],
  };
}

/** The tool deps over the real session API; the status source has nothing to say. */
export function depsOf(plugin: Plugin, session: Session): object {
  return {
    session,
    resolve: plugin.role.resolveRole,
    status: {
      watch: () => Promise.resolve({ asks: [], childBusyMs: undefined }),
      lastMessage: () => Promise.resolve(undefined),
    },
  };
}

/** Runs one tool as `sessionID`; its content, or the error it threw. */
export async function runTool(
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

/** The first line of `text` that starts with `pm-<code> `, or undefined. */
export function lineOf(
  text: string | undefined,
  code: string,
): string | undefined {
  return (text ?? "")
    .split("\n")
    .find((line) => line.startsWith(`pm-${code} `));
}
