import { reportState } from "./report.ts";
import type { ReportState } from "./report.ts";
import { NotAChildError, UnknownSessionError } from "./wait-types.ts";
import type { HistoryItem, Host } from "./wait-types.ts";

export function noop(): void {
  // a sleep ended by its own abort has nothing to report
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function checkChild(
  id: string,
  callerID: string,
  host: Host,
): Promise<void> {
  let session: { id: string; parentID?: string } | undefined;
  try {
    session = await host.getSession(id);
  } catch (error) {
    throw new Error(`could not look up session ${id}: ${messageOf(error)}`, {
      cause: error,
    });
  }
  if (session === undefined) {
    throw new UnknownSessionError(`session ${id} does not exist`);
  }
  if (session.parentID !== callerID) {
    throw new NotAChildError(
      `session ${id} is not a child of the calling session ${callerID}, its parent is ${session.parentID ?? "none"}`,
    );
  }
}

/** The subagent reports in a history, in order, for the wanted children. */
export function reportsIn(
  items: HistoryItem[],
  wanted: ReadonlySet<string>,
): { childID: string; state: ReportState }[] {
  const found: { childID: string; state: ReportState }[] = [];
  for (const { childID, source, text } of items) {
    const state = source === "subagent" ? reportState(text) : undefined;
    if (state !== undefined && childID !== undefined && wanted.has(childID)) {
      found.push({ childID, state });
    }
  }
  return found;
}

/** Resolves when the signal aborts; the caller rethrows `signal.reason` itself. */
export function abortion(signal: AbortSignal): {
  aborted: Promise<void>;
  dispose: () => void;
} {
  let onAbort: () => void = noop;
  const aborted = new Promise<void>((resolve) => {
    onAbort = resolve;
  });
  signal.addEventListener("abort", onAbort, { once: true });
  return {
    aborted,
    dispose: () => {
      signal.removeEventListener("abort", onAbort);
    },
  };
}
