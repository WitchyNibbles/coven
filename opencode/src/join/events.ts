import { ownString } from "./fields.ts";
import type { Observed } from "./record.ts";
import { reportState } from "./report.ts";
import type { ReportState } from "./report.ts";

const SESSION_EVENTS = {
  "session.execution.started": "started",
  "session.execution.succeeded": "ended",
  "session.execution.failed": "ended",
  "session.execution.interrupted": "ended",
  "session.deleted": "removed",
} as const;

const INBOX_EVENT = "session.inbox.enqueued";

/** The 2.3.2 event types the join reads; the subscription asks for exactly these. */
export const EVENT_TYPES: readonly string[] = [
  ...Object.keys(SESSION_EVENTS),
  INBOX_EVENT,
];

const STATES: readonly ReportState[] = [
  "running",
  "completed",
  "error",
  "cancelled",
];

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields | undefined {
  return typeof value === "object" && value !== null
    ? (value as Fields)
    : undefined;
}

// The tag in the report text wins; the metadata state is only for an unreadable tag.
function stateOf(
  body: string | undefined,
  metadata: Fields | undefined,
): ReportState | undefined {
  const declared = ownString(metadata, "state");
  return reportState(body ?? "") ?? STATES.find((state) => state === declared);
}

function normalizeReport(data: Fields | undefined): Observed | undefined {
  const payload = fields(fields(data?.["item"])?.["payload"]);
  const metadata = fields(payload?.["metadata"]);
  const parentID = ownString(data, "sessionID");
  const childID = ownString(metadata, "childID");
  const state = stateOf(ownString(payload, "text"), metadata);
  if (
    ownString(metadata, "source") !== "subagent" ||
    parentID === undefined ||
    childID === undefined ||
    state === undefined
  ) {
    return undefined;
  }
  return {
    kind: "report",
    parentID,
    childID,
    state,
    description: ownString(payload, "description") ?? "",
  };
}

function isSessionEvent(type: string): type is keyof typeof SESSION_EVENTS {
  return type in SESSION_EVENTS;
}

/** Maps one raw 2.3.2 event to what the record observes, or `undefined`. */
export function normalize(event: unknown): Observed | undefined {
  const root = fields(event);
  const type = ownString(root, "type") ?? "";
  const data = fields(root?.["data"]);
  if (type === INBOX_EVENT) {
    return normalizeReport(data);
  }
  const sessionID = ownString(data, "sessionID");
  if (!isSessionEvent(type) || sessionID === undefined) {
    return undefined;
  }
  return { kind: SESSION_EVENTS[type], sessionID };
}
