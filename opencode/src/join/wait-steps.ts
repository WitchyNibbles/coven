import type { JoinRecord } from "./record.ts";
import { reportState } from "./report.ts";
import type { ReportState } from "./report.ts";
import { NotAChildError, UnknownSessionError } from "./wait-types.ts";
import type { HistoryItem, View } from "./wait-types.ts";

export function noop(): void {
  // a sleep ended by its own abort has nothing to report
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function lookupFailed(id: string): (error: unknown) => never {
  return (error) => {
    throw new Error(`could not look up session ${id}: ${messageOf(error)}`, {
      cause: error,
    });
  };
}

export function checkChild(
  id: string,
  callerID: string,
  session: { id: string; parentID?: string } | undefined,
): void {
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
  aborted: Promise<undefined>;
  dispose: () => void;
} {
  let onAbort: () => void = noop;
  const aborted = new Promise<undefined>((resolve) => {
    onAbort = () => {
      resolve(undefined);
    };
  });
  signal.addEventListener("abort", onAbort, { once: true });
  return {
    aborted,
    dispose: () => {
      signal.removeEventListener("abort", onAbort);
    },
  };
}

/** The record as it stands, with no host call: what a timeout reports. */
export function snapshot(
  record: JoinRecord,
  callerID: string,
  ids: readonly string[],
): View {
  const view: View = { done: [], pending: [] };
  for (const id of ids) {
    const status = record.status(callerID, id);
    if (status.end === undefined) {
      view.pending.push(status);
    } else {
      const { sessionID, description, end } = status;
      view.done.push({ sessionID, state: end, description });
    }
  }
  return view;
}
