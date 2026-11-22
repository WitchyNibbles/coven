import type { JoinRecord } from "./record.ts";
import type { Host } from "./wait-types.ts";

function noReconcile(): void {
  // a failed watch leaves the child to the event stream, as before the watch
}

function markEnded(record: JoinRecord, parentID: string, id: string): void {
  const { end, endedAt } = record.status(parentID, id);
  if (end === undefined && endedAt === undefined) {
    record.observe({ kind: "ended", sessionID: id });
  }
}

/**
 * Records each child as ended once OpenCode says it is idle, so a child whose end and report
 * events were both missed still reaches the no-report grace. Nothing is recorded once `signal`
 * has aborted, and a failed watch only means no reconcile for that child.
 */
export function watchIdle(
  host: Host,
  record: JoinRecord,
  parentID: string,
  ids: readonly string[],
  signal: AbortSignal,
): void {
  for (const id of ids) {
    void Promise.resolve()
      .then(() => host.waitIdle(id, signal))
      .then(() => {
        if (!signal.aborted) {
          markEnded(record, parentID, id);
        }
      }, noReconcile);
  }
}
