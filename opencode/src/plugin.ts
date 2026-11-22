import type { PluginContext, ToolDefinition } from "./join/context.ts";
import { normalize } from "./join/events.ts";
import { createHost } from "./join/host.ts";
import { inputSchema, parseInput } from "./join/input.ts";
import { JoinRecord } from "./join/record.ts";
import { reminderText } from "./join/reminder.ts";
import { follow } from "./join/subscription.ts";
import { noop } from "./join/wait-steps.ts";
import { awaitAgents } from "./join/wait.ts";
import type { Host } from "./join/wait.ts";

const FORGET_EVERY_MS = 60 * 60 * 1000;

const DESCRIPTION =
  "Waits until the given subagents send their final report, then returns. " +
  "It returns states only; the reports themselves are in the conversation as <subagent> messages. " +
  "On a repeat call pass only the ids still pending. " +
  "After a server restart the wait is best effort.";

export interface Deps {
  now: () => number;
}

/** Looks each session up once while the record holds it, failures included, to learn which ones are children. */
class Children {
  private readonly seen = new Set<string>();
  private readonly ctx: PluginContext;
  private readonly record: JoinRecord;

  constructor(ctx: PluginContext, record: JoinRecord) {
    this.ctx = ctx;
    this.record = record;
  }

  link(sessionID: string): void {
    if (this.seen.has(sessionID)) {
      return;
    }
    this.seen.add(sessionID);
    void this.ctx.session.get({ sessionID }).then((session) => {
      if (session.parentID !== undefined) {
        this.record.linkChild(session.parentID, sessionID, session.title ?? "");
      }
    }, noop);
  }

  drop(sessionID: string): void {
    this.seen.delete(sessionID);
  }
}

function awaitTool(record: JoinRecord, host: Host): ToolDefinition {
  return {
    name: "await_agents",
    description: DESCRIPTION,
    input: inputSchema,
    options: { codemode: false },
    execute: async (raw, context) => {
      const input = parseInput(raw, context.sessionID);
      const result = await awaitAgents(
        input,
        {
          callerID: context.sessionID,
          signal: context.signal,
          progress: (status) => {
            void context.progress({ status }).catch(noop);
          },
        },
        record,
        host,
      );
      return { content: JSON.stringify(result) };
    },
  };
}

/** Wires the join into one plugin context; the returned function undoes it. */
export async function joinSetup(
  ctx: PluginContext,
  deps: Deps,
): Promise<() => void> {
  const record = new JoinRecord(deps.now);
  const children = new Children(ctx, record);
  await ctx.session.hook("context", (event) => {
    const text = reminderText(record.running(event.sessionID));
    if (text !== undefined) {
      event.system.push({ type: "text", text });
    }
  });
  await ctx.tool.transform((editor) => {
    editor.add(awaitTool(record, createHost(ctx, deps.now)));
  });
  const stop = new AbortController();
  void follow(ctx, stop.signal, (event) => {
    const observed = normalize(event);
    if (observed === undefined) {
      return;
    }
    record.observe(observed);
    if (observed.kind === "started") {
      children.link(observed.sessionID);
    } else if (observed.kind === "removed") {
      children.drop(observed.sessionID);
    }
  });
  const forgetter = setInterval(() => {
    for (const sessionID of record.forget()) {
      children.drop(sessionID);
    }
  }, FORGET_EVERY_MS);
  return () => {
    stop.abort();
    clearInterval(forgetter);
  };
}

export default {
  id: "coven.join",
  setup: (ctx: PluginContext): Promise<() => void> =>
    joinSetup(ctx, { now: () => Date.now() }),
};
