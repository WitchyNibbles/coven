import type { CovenContext } from "./context.ts";
import { checkPython, guardsHook } from "./guard.ts";
import type { SpawnFn } from "./guard.ts";
import { joinSetup } from "./plugin.ts";
import { parseOptions } from "./options.ts";
import { pingTool } from "./ping.ts";
import { resolveRole } from "./pm/role.ts";
import { pmSetup } from "./pm/setup.ts";
import { toolChoiceHook } from "./tool-choice.ts";

export interface CovenDeps {
  now: () => number;
  log: (line: string) => void;
  /** Where an undeliverable notice goes; the serve folder's `log` unless a test stands in. */
  serveLog?: (line: string) => void;
  /** Starts the guard child; the real `spawn` unless a test stands in. */
  spawn?: SpawnFn;
}

const REQUIRED_TOOLS = [
  "coven_ping",
  "await_agents",
  "coven_gm_claim",
  "coven_send",
  "coven_pm_launch",
  "coven_pm_list",
  "coven_pm_resume",
  "coven_pm_stop",
] as const;

/**
 * 2.3.2 has no HTTP tool list, so a plugin reported active proves every tool is there. The hooks
 * need `python3` 3.12 or newer: with less, pm-permissions would allow what it could not check.
 */
async function selfCheck(ctx: CovenContext, spawn?: SpawnFn): Promise<void> {
  await checkPython(spawn);
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
  let stopPm: (() => void) | undefined;
  const cleanup = (): void => {
    try {
      stopPm?.();
    } finally {
      stopJoin();
    }
  };
  try {
    await ctx.tool.transform((editor) => {
      editor.add(pingTool(options.covenRoot));
    });
    stopPm = await pmSetup(ctx, options, {
      now: deps.now,
      log: deps.log,
      ...(deps.serveLog === undefined ? {} : { serveLog: deps.serveLog }),
      beforeTool: guardsHook(options, {
        directory: ctx.location.directory,
        resolve: (sessionID) =>
          resolveRole(sessionID, (id) => ctx.session.get({ sessionID: id })),
        ...(deps.spawn === undefined ? {} : { spawn: deps.spawn }),
      }),
    });
    const warned = new Set<string>();
    for (const providerID of options.toolChoiceAuto) {
      await ctx.session.hook("http.request", toolChoiceHook(deps.log, warned), {
        providerID,
      });
    }
    await selfCheck(ctx, deps.spawn);
  } catch (error) {
    cleanup();
    throw error;
  }
  deps.log(`coven plugin loaded from ${options.covenRoot}`);
  return cleanup;
}
