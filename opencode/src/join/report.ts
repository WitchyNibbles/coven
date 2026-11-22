export type ReportState = "running" | "completed" | "error" | "cancelled";

export type EndState =
  "completed" | "error" | "cancelled" | "ended-without-report" | "removed";

const REPORT_STATES: readonly string[] = [
  "running",
  "completed",
  "error",
  "cancelled",
];

const SUBAGENT_TAG = /<subagent((?:\s+[\w:-]+="[^"]*")*)\s*>/;
const ATTRIBUTE = /\s([\w:-]+)="([^"]*)"/g;

function isReportState(value: string): value is ReportState {
  return REPORT_STATES.includes(value);
}

/** Reads `state="..."` from the first `<subagent ...>` tag in a report. */
export function reportState(text: string): ReportState | undefined {
  const attributes = SUBAGENT_TAG.exec(text)?.[1];
  if (attributes === undefined) {
    return undefined;
  }
  for (const match of attributes.matchAll(ATTRIBUTE)) {
    const [, name, value] = match;
    if (name === "state" && value !== undefined && isReportState(value)) {
      return value;
    }
  }
  return undefined;
}
