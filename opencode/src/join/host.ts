import type { ContextMessage, PluginContext } from "./context.ts";
import { ownString } from "./fields.ts";
import type { HistoryItem, Host } from "./wait-types.ts";

const MAX_CAUSE_DEPTH = 3;

/** A timer that ends early, quietly, when `signal` aborts. */
export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(done, ms);
    function done(): void {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

function isNotFound(error: unknown, depth = 0): boolean {
  if (typeof error !== "object" || error === null || depth > MAX_CAUSE_DEPTH) {
    return false;
  }
  const { _tag, name, cause } = error as Record<string, unknown>;
  return (
    _tag === "Session.NotFoundError" ||
    name === "Session.NotFoundError" ||
    isNotFound(cause, depth + 1)
  );
}

function toHistoryItem(message: ContextMessage): HistoryItem | undefined {
  if (typeof message.text !== "string") {
    return undefined;
  }
  const item: HistoryItem = { text: message.text };
  const childID = ownString(message.metadata, "childID");
  const source = ownString(message.metadata, "source");
  if (childID !== undefined) {
    item.childID = childID;
  }
  if (source !== undefined) {
    item.source = source;
  }
  return item;
}

function assistantText(message: ContextMessage | undefined): string {
  return (message?.content ?? [])
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "")
    .join("\n");
}

/** What `awaitAgents` needs from OpenCode, read through the plugin context. */
export function createHost(ctx: PluginContext, now: () => number): Host {
  return {
    getSession: async (sessionID) => {
      try {
        return await ctx.session.get({ sessionID });
      } catch (error) {
        if (isNotFound(error)) {
          return undefined;
        }
        throw error;
      }
    },
    history: async (sessionID) =>
      (await ctx.session.context({ sessionID })).flatMap((message) => {
        const item = toHistoryItem(message);
        return item === undefined ? [] : [item];
      }),
    lastAssistantText: async (sessionID) =>
      assistantText(
        (await ctx.session.context({ sessionID }))
          .filter((message) => message.type === "assistant")
          .at(-1),
      ),
    waitIdle: (sessionID, signal) =>
      ctx.session.wait({ sessionID }, { signal }),
    now,
    sleep,
  };
}
