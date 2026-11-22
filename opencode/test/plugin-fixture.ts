import { setImmediate as tick } from "node:timers/promises";

import type {
  ContextHookEvent,
  ContextMessage,
  PluginContext,
  SessionInfo,
  ToolCallContext,
  ToolDefinition,
} from "../src/join/context.ts";
import { joinSetup } from "../src/plugin.ts";

export const LEAD = "ses_lead";
export const KID = "ses_kid";

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

export async function boot(clock = { now: 1_000_000 }) {
  const streams: Stream[] = [];
  const subscriptions: { types: readonly string[]; signal: AbortSignal }[] = [];
  const sessions = new Map<string, SessionInfo | Error>([
    [KID, { id: KID, parentID: LEAD, title: "kid" }],
  ]);
  const messages = new Map<string, ContextMessage[]>();
  const lookups: string[] = [];
  const hooks: ((event: ContextHookEvent) => void)[] = [];
  const tools: ToolDefinition[] = [];
  const ctx: PluginContext = {
    event: {
      subscribe: (options) => {
        subscriptions.push(options);
        const stream = new Stream();
        streams.push(stream);
        return stream.iterate(options.signal);
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
      hook: (_name, callback) => {
        hooks.push(callback);
        return Promise.resolve({});
      },
    },
    tool: {
      transform: (callback) => {
        callback({ add: (tool) => tools.push(tool) });
        return Promise.resolve({});
      },
    },
  };
  const cleanup = await joinSetup(ctx, { now: () => clock.now });
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
    ctx,
    clock,
    streams,
    subscriptions,
    sessions,
    messages,
    lookups,
    hooks,
    tools,
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
