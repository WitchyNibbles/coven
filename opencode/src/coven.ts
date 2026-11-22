import type { CovenContext, HttpRequestEvent } from "./context.ts";
import { noop } from "./join/noop.ts";
import { joinSetup } from "./plugin.ts";
import { parseOptions } from "./options.ts";
import { pingTool } from "./ping.ts";

export interface CovenDeps {
  now: () => number;
  log: (line: string) => void;
}

const REQUIRED_TOOLS = ["coven_ping", "await_agents"] as const;

/** Stub: the tool-choice rewrite is filled in later. */
const toolChoiceHook: (event: HttpRequestEvent) => void = noop;

/** 2.3.2 has no HTTP tool list, so a plugin reported active proves both tools are there. */
async function selfCheck(ctx: CovenContext): Promise<void> {
  const listed = new Set((await ctx.tool.list()).map((tool) => tool.id));
  const missing = REQUIRED_TOOLS.filter((name) => !listed.has(name));
  if (missing.length > 0) {
    throw new Error(`coven: tools not registered: ${missing.join(", ")}`);
  }
}

/** Wires the whole coven plugin into one context; the returned function undoes it. */
export async function covenSetup(
  ctx: CovenContext,
  deps: CovenDeps,
  rawOptions: unknown,
): Promise<() => void> {
  const options = parseOptions(rawOptions);
  const cleanup = await joinSetup(ctx, { now: deps.now });
  try {
    await ctx.tool.transform((editor) => {
      editor.add(pingTool(options.covenRoot));
    });
    for (const providerID of options.toolChoiceAuto) {
      await ctx.session.hook("http.request", toolChoiceHook, { providerID });
    }
    await selfCheck(ctx);
  } catch (error) {
    cleanup();
    throw error;
  }
  deps.log(`coven plugin loaded from ${options.covenRoot}`);
  return cleanup;
}
