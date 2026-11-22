import { messageOf } from "./fields.ts";
import type { JoinRecord } from "./record.ts";
import { reportState } from "./report.ts";
import type { ReportState } from "./report.ts";
import { NotAChildError, UnknownSessionError } from "./wait-types.ts";
import type { HistoryItem, View } from "./wait-types.ts";

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
      `session ${id} is not a child of the calling session`,
    );
  }
}

/** The subagent reports in a history, in order, for the wanted children. */
function reportsIn(
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

/** Feeds each report in `items` that the record has not seen yet, counting per child in order. */
export function feedReports(
  record: JoinRecord,
  callerID: string,
  ids: readonly string[],
  items: HistoryItem[],
): void {
  const seen = new Map<string, number>();
  for (const { childID, state } of reportsIn(items, new Set(ids))) {
    const nth = (seen.get(childID) ?? 0) + 1;
    seen.set(childID, nth);
    if (record.reportCount(callerID, childID) < nth) {
      record.observe({
        kind: "report",
        parentID: callerID,
        childID,
        state,
        description: record.status(callerID, childID).description,
      });
    }
  }
}

/**
 * Resolves when `signal` aborts, and stops listening once `until` aborts; the caller rethrows
 * `signal.reason` itself.
 */
export function whenAborted(
  signal: AbortSignal,
  until: AbortSignal,
): Promise<undefined> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(undefined);
      return;
    }
    signal.addEventListener(
      "abort",
      () => {
        resolve(undefined);
      },
      { once: true, signal: until },
    );
  });
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
