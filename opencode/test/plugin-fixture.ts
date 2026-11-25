import { setImmediate as tick } from "node:timers/promises";

import { covenSetup } from "../src/coven.ts";
import type {
  CovenContext,
  ExecuteBeforeEvent,
  ShellCreateEvent,
} from "../src/context.ts";
import type {
  ContextHookEvent,
  ContextMessage,
  SessionInfo,
  ToolCallContext,
  ToolDefinition,
} from "../src/join/context.ts";
import { joinSetup } from "../src/plugin.ts";
import { exits, fakeSpawn } from "./guard-fake.ts";
import type { Behaviour } from "./guard-fake.ts";

export const LEAD = "ses_lead";
export const KID = "ses_kid";
export const DIRECTORY = "/work/session";

class Stream {
  private readonly queue: unknown[] = [];
  private wake: (() => void) | undefined;
  private over = false;

  push(event: unknown): void {
    this.queue.push(event);
    this.wake?.();
  }

  end(): void {
    this.over = true;
    this.wake?.();
  }

  async *iterate(signal: AbortSignal): AsyncGenerator<unknown> {
    signal.addEventListener("abort", () => this.end());
    for (;;) {
      const next = this.queue.shift();
      if (next !== undefined) {
        yield next;
      } else if (this.over) {
        return;
      } else {
        await new Promise<void>((resolve) => {
          this.wake = resolve;
        });
      }
    }
  }
}

export function notFound(): Error {
  return Object.assign(new Error("session not found"), {
    _tag: "Session.NotFoundError",
  });
}

export function started(sessionID: string): unknown {
  return { type: "session.execution.started", data: { sessionID } };
}

export function ended(sessionID: string): unknown {
  return { type: "session.execution.succeeded", data: { sessionID } };
}

export function report(
  parentID: string,
  childID: string,
  state: string,
): unknown {
  return {
    type: "session.inbox.enqueued",
    data: {
      sessionID: parentID,
      item: {
        type: "synthetic",
        payload: {
          text: `<subagent sessionID="${childID}" state="${state}" description="kid">\nbody\n</subagent>`,
          description: "kid",
          metadata: { source: "subagent", childID, state },
        },
      },
    },
  };
}

export interface Registered {
  name: string;
  options: unknown;
}

export interface ToolHook {
  name: string;
  callback: (event: ExecuteBeforeEvent) => Promise<void> | void;
  disposed: number;
}

export interface ShellHook {
  name: string;
  callback: (event: ShellCreateEvent) => Promise<void> | void;
}

interface Setup {
  clock?: { now: number };
  options?: Readonly<Record<string, unknown>>;
  /** Tool names the fake `tool.list` leaves out, as if they never registered. */
  unlisted?: readonly string[];
  /** What each guard child the fake spawn starts does; it allows the call by default. */
  guard?: Behaviour;
}

function makeWorld({
  clock = { now: 1_000_000 },
  options,
  unlisted = [],
  guard = exits(0),
}: Setup) {
  const streams: Stream[] = [];
  const subscriptions: { types: readonly string[]; signal: AbortSignal }[] = [];
  const sessions = new Map<string, SessionInfo | Error>([
    [KID, { id: KID, parentID: LEAD, title: "kid" }],
  ]);
  const messages = new Map<string, ContextMessage[]>();
  const lookups: string[] = [];
  const hooks: ((event: ContextHookEvent) => void)[] = [];
  const tools: ToolDefinition[] = [];
  const registered: Registered[] = [];
  const toolHooks: ToolHook[] = [];
  const shellHooks: ShellHook[] = [];
  const fake = fakeSpawn(guard);
  const ctx: CovenContext = {
    ...(options === undefined ? {} : { options }),
    location: { directory: DIRECTORY },
    event: {
      subscribe: (subscription) => {
        subscriptions.push(subscription);
        const stream = new Stream();
        streams.push(stream);
        return stream.iterate(subscription.signal);
      },
    },
    session: {
      get: ({ sessionID }) => {
        lookups.push(sessionID);
        const found = sessions.get(sessionID);
        return found instanceof Error
          ? Promise.reject(found)
          : found === undefined
            ? Promise.reject(notFound())
            : Promise.resolve(found);
      },
      wait: () => new Promise<void>(() => undefined),
      context: ({ sessionID }) =>
        Promise.resolve(messages.get(sessionID) ?? []),
      hook: ((
        name: string,
        callback: (event: ContextHookEvent) => void,
        hookOptions?: unknown,
      ) => {
        registered.push({ name, options: hookOptions });
        if (name === "context") {
          hooks.push(callback);
        }
        return Promise.resolve({});
      }) as CovenContext["session"]["hook"],
    },
    shell: {
      hook: (name, callback) => {
        shellHooks.push({ name, callback });
        return Promise.resolve({});
      },
    },
    tool: {
      transform: (callback) => {
        callback({ add: (tool) => tools.push(tool) });
        return Promise.resolve({});
      },
      hook: (name, callback) => {
        const entry: ToolHook = { name, callback, disposed: 0 };
        toolHooks.push(entry);
        return Promise.resolve({
          dispose: () => {
            entry.disposed += 1;
          },
        });
      },
      list: () =>
        Promise.resolve(
          tools
            .filter((tool) => !unlisted.includes(tool.name))
            .map((tool) => ({ id: tool.name })),
        ),
    },
  };
  return {
    ctx,
    clock,
    streams,
    subscriptions,
    sessions,
    messages,
    lookups,
    hooks,
    tools,
    registered,
    toolHooks,
    shellHooks,
    spawn: fake.spawn,
    spawned: fake.calls,
  };
}

function drive(world: ReturnType<typeof makeWorld>, cleanup: () => void) {
  const { streams, tools, hooks } = world;
  const reminder = (sessionID: string): string[] => {
    const event: ContextHookEvent = { sessionID, system: [] };
    hooks.forEach((hook) => hook(event));
    return event.system.map((part) => part.text);
  };
  const call = (
    input: unknown,
    sessionID = LEAD,
    signal = new AbortController().signal,
  ) => {
    const progress: string[] = [];
    const context: ToolCallContext = {
      sessionID,
      signal,
      progress: (update) => {
        progress.push(update.status);
        return Promise.resolve();
      },
    };
    const tool = tools[0];
    if (tool === undefined) {
      throw new Error("await_agents is not registered");
    }
    return { progress, result: tool.execute(input, context) };
  };
  return {
    ...world,
    cleanup,
    reminder,
    call,
    emit: async (event: unknown, stream = 0) => {
      streams[stream]?.push(event);
      await tick();
      await tick();
    },
  };
}

export async function boot(clock = { now: 1_000_000 }) {
  const world = makeWorld({ clock });
  const cleanup = await joinSetup(world.ctx, { now: () => clock.now });
  return drive(world, cleanup);
}

/** The whole coven plugin; setup may reject, so the world is returned beside the outcome. */
export function bootCoven(setup: Setup = {}) {
  const world = makeWorld(setup);
  const logs: string[] = [];
  const outcome = covenSetup(
    world.ctx,
    {
      now: () => world.clock.now,
      log: (line) => logs.push(line),
      spawn: world.spawn,
    },
    world.ctx.options,
  );
  return { ...world, logs, outcome };
}
