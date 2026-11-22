export type ReportState = "running" | "completed" | "error" | "cancelled";

export type EndState =
  "completed" | "error" | "cancelled" | "ended-without-report" | "removed";

const REPORT_STATES: readonly string[] = [
  "running",
  "completed",
  "error",
  "cancelled",
];

// OpenCode writes `<subagent sessionID="..." state="..." description="...">` with the
// description unescaped, so only the leading two attributes are read; whatever follows
// (quotes, `>`) is ignored.
const LEADING_ATTRIBUTES = /<subagent\s+sessionID="[^"]*"\s+state="([^"]*)"/;

export function isReportState(value: string): value is ReportState {
  return REPORT_STATES.includes(value);
}

/** Reads `state="..."` from the first `<subagent ...>` tag in a report. */
export function reportState(text: string): ReportState | undefined {
  const state = LEADING_ATTRIBUTES.exec(text)?.[1];
  return state !== undefined && isReportState(state) ? state : undefined;
}
