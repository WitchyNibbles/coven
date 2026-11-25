import { appendFileSync } from "node:fs";
import { join } from "node:path";

import type { CovenContext, ExecuteBeforeEvent } from "../context.ts";
import { messageOf } from "../join/fields.ts";
import { createHost } from "../join/host.ts";
import { follow } from "../join/subscription.ts";
import type { CovenOptions } from "../options.ts";
import { askToDeny } from "./asks.ts";
import { pmLaunchTool } from "./launch.ts";
import { pmListTool, pmResumeTool, pmStopTool } from "./manage.ts";
import { NoticeTracker } from "./notices.ts";
import { resolveRole } from "./role.ts";
import { shellEnvHooks } from "./shell-env.ts";
import { sharedState } from "./state.ts";
import { serverStatus } from "./status.ts";
import { gmClaimTool, sendTool } from "./tools.ts";

export interface PmDeps {
  now: () => number;
  /** The server's own log (stderr): where a failed write to the serve log is reported. */
  log: (line: string) => void;
  /** Where a notice that cannot be delivered goes; the serve folder's `log` unless a test stands in. */
  serveLog?: (line: string) => void;
  /** The guards of every tool call: deny-secrets, the server guard and, for a PM tree, the PM hooks. */
  beforeTool: (event: ExecuteBeforeEvent) => Promise<void>;
}

/** Appends a stamped line to `<serveDir>/log`; a line that cannot be written goes to `deps.log`. */
export function serveLogger(
  serveDir: string,
  deps: Pick<PmDeps, "now" | "log">,
): (line: string) => void {
  return (line) => {
    const stamped = `${new Date(deps.now()).toISOString()} ${line}`;
    try {
      appendFileSync(join(serveDir, "log"), `${stamped}\n`);
    } catch (error) {
      deps.log(`${stamped} (serve log unwritable: ${messageOf(error)})`);
    }
  };
}

/**
 * Wires the hierarchy into one plugin context: the GM and PM tools, the shell environment hooks
 * (the role variable), the evaluate hook (a PM's ask becomes a deny) and the turn-end notices.
 * The tools' guards run first in the one `execute.before` hook. The returned function undoes it.
 */
export async function pmSetup(
  ctx: CovenContext,
  options: CovenOptions,
  deps: PmDeps,
): Promise<() => void> {
  const get = (sessionID: string) => ctx.session.get({ sessionID });
  const host = createHost(ctx, deps.now);
  const toolDeps = { session: ctx.session, resolve: resolveRole };
  const state = sharedState();
  const shell = shellEnvHooks({
    keyEnv: options.keyEnv,
    directory: ctx.location.directory,
    queue: state.queue,
    now: deps.now,
    get,
  });
  const status = serverStatus({
    session: ctx.session,
    permission: ctx.permission,
    lastText: (id) => host.lastAssistantText(id),
    now: deps.now,
  });
  await ctx.tool.transform((editor) => {
    editor.add(gmClaimTool(toolDeps));
    editor.add(sendTool(toolDeps));
    editor.add(
      pmLaunchTool({
        ...toolDeps,
        options: {
          store: options.store,
          profileDir: options.profileDir,
          secretRules: options.secretRules,
        },
      }),
    );
    editor.add(pmListTool({ ...toolDeps, status }));
    editor.add(pmResumeTool(toolDeps));
    editor.add(pmStopTool(toolDeps));
  });
  const guard = await ctx.tool.hook("execute.before", async (event) => {
    await deps.beforeTool(event);
    await shell.executeBefore(event);
  });
  const stop = new AbortController();
  const cleanup = (): void => {
    stop.abort();
    guard.dispose();
  };
  try {
    await ctx.shell.hook("create.before", (event) => shell.createBefore(event));
    await ctx.permission.hook(
      "evaluate",
      askToDeny((id) => resolveRole(id, get)),
    );
    const tracker = new NoticeTracker(ctx.location.directory, {
      session: ctx.session,
      state,
      lastText: (id) => host.lastAssistantText(id),
      log: deps.serveLog ?? serveLogger(options.serveDir, deps),
    });
    void follow(ctx, stop.signal, (event) => {
      tracker.onEvent(event).catch((error: unknown) => {
        deps.log(`coven notice failed: ${messageOf(error)}`);
      });
    });
  } catch (error) {
    cleanup();
    throw error;
  }
  return cleanup;
}
