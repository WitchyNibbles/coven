import { appendFileSync } from "node:fs";
import { join } from "node:path";

import type {
  CovenContext,
  ExecuteBeforeEvent,
  Registration,
} from "../context.ts";
import { isPlainObject, messageOf } from "../join/fields.ts";
import { createHost } from "../join/host.ts";
import { follow } from "../join/subscription.ts";
import type { CovenOptions } from "../options.ts";
import { askToDeny } from "./asks.ts";
import { pmLaunchTool } from "./launch.ts";
import { pmListTool, pmResumeTool, pmStopTool } from "./manage.ts";
import { NoticeTracker } from "./notices.ts";
import { resolvePlaced, resolveRole } from "./role.ts";
import type { RoleLookup } from "./role.ts";
import { pmIsolation, shellEnvHooks } from "./shell-env.ts";
import { sharedState } from "./state.ts";
import { serverStatus } from "./status.ts";
import { gmClaimTool, sendTool } from "./tools.ts";
import { stopTreesIn } from "./trees.ts";

export interface PmDeps {
  now: () => number;
  /** The server's own log (stderr): where a failed write to the serve log is reported. */
  log: (line: string) => void;
  /** Where a notice that cannot be delivered goes; the serve folder's `log` unless a test stands in. */
  serveLog?: (line: string) => void;
  /**
   * The guards of every tool call: deny-secrets, the server guard and, for a PM tree, the PM
   * hooks. `role` is the call's one role lookup.
   */
  beforeTool: (event: ExecuteBeforeEvent, role: RoleLookup) => Promise<void>;
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

/** Removes a hook registration, when the runtime handed back one that can be removed. */
function dispose(registration: unknown): void {
  if (isPlainObject(registration)) {
    (registration as Partial<Registration>).dispose?.();
  }
}

/** A lookup that runs on first use and gives every later caller the same promise. */
function once<T>(make: () => Promise<T>): () => Promise<T> {
  let lookup: Promise<T> | undefined;
  return () => (lookup ??= make());
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
  const startedAt = deps.now();
  const get = (sessionID: string) => ctx.session.get({ sessionID });
  const host = createHost(ctx, deps.now);
  const lastText = (id: string) => host.lastAssistantText(id);
  const toolDeps = { session: ctx.session, resolve: resolveRole };
  const state = sharedState();
  const isolation = pmIsolation(options);
  const shell = shellEnvHooks({
    keyEnv: options.keyEnv,
    directory: ctx.location.directory,
    isolation,
    queue: state.queue,
    taken: state.taken,
    now: deps.now,
  });
  const status = serverStatus({
    session: ctx.session,
    permission: ctx.permission,
    lastText,
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
    editor.add(
      pmStopTool({ ...toolDeps, trees: stopTreesIn(isolation.pidsDir) }),
    );
  });
  const guard = await ctx.tool.hook("execute.before", async (event) => {
    // looked up when a hook first asks, so a call no hook needs a role for makes no lookup
    const placed = once(() => resolvePlaced(event.sessionID, get));
    const role = async () => (await placed()).info;
    const holder = async () => (await placed()).holder;
    await deps.beforeTool(event, role);
    await shell.executeBefore(event, role, holder);
  });
  const stop = new AbortController();
  const registrations: unknown[] = [];
  const cleanup = (): void => {
    stop.abort();
    try {
      guard.dispose();
    } finally {
      registrations.forEach(dispose);
    }
  };
  try {
    // each is kept as soon as it exists: a later one that fails must not leak it
    registrations.push(
      await ctx.shell.hook("create.before", (event) =>
        shell.createBefore(event),
      ),
    );
    registrations.push(
      await ctx.permission.hook(
        "evaluate",
        askToDeny((id) => resolveRole(id, get)),
      ),
    );
    const tracker = new NoticeTracker(ctx.location.directory, {
      session: ctx.session,
      state,
      lastText,
      startedAt,
      log: deps.serveLog ?? serveLogger(options.serveDir, deps),
    });
    void follow(ctx, stop.signal, (event) => {
      tracker.onEvent(event).catch((error: unknown) => {
        deps.log(`coven notice failed: ${messageOf(error)}`);
      });
    });
  } catch (error) {
    try {
      cleanup();
    } catch (cleanupError) {
      // the setup's own error is the one to report; the teardown's is only noted
      deps.log(`coven setup cleanup failed: ${messageOf(cleanupError)}`);
    }
    throw error;
  }
  return cleanup;
}
