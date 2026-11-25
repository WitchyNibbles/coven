import type { CovenContext, Registration } from "./context.ts";
import { denySecretsHook } from "./guard.ts";
import type { SpawnFn } from "./guard.ts";
import { joinSetup } from "./plugin.ts";
import { parseOptions } from "./options.ts";
import { pingTool } from "./ping.ts";
import { scrubShellEnv } from "./shell-env.ts";
import { toolChoiceHook } from "./tool-choice.ts";

export interface CovenDeps {
  now: () => number;
  log: (line: string) => void;
  /** Starts the guard child; the real `spawn` unless a test stands in. */
  spawn?: SpawnFn;
}

const REQUIRED_TOOLS = ["coven_ping", "await_agents"] as const;

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
  const stopJoin = await joinSetup(ctx, { now: deps.now });
  let guard: Registration | undefined;
  const cleanup = (): void => {
    try {
      guard?.dispose();
    } finally {
      stopJoin();
    }
  };
  try {
    await ctx.tool.transform((editor) => {
      editor.add(pingTool(options.covenRoot));
    });
    guard = await ctx.tool.hook(
      "execute.before",
      denySecretsHook(options, {
        directory: ctx.location.directory,
        ...(deps.spawn === undefined ? {} : { spawn: deps.spawn }),
      }),
    );
    await ctx.shell.hook("create.before", scrubShellEnv(options.keyEnv));
    const warned = new Set<string>();
    for (const providerID of options.toolChoiceAuto) {
      await ctx.session.hook("http.request", toolChoiceHook(deps.log, warned), {
        providerID,
      });
    }
    await selfCheck(ctx);
  } catch (error) {
    cleanup();
    throw error;
  }
  deps.log(`coven plugin loaded from ${options.covenRoot}`);
  return cleanup;
}
