import type { ChildStatus } from "./record.ts";

function label(child: ChildStatus): string {
  return child.description === ""
    ? child.sessionID
    : `${child.description} (${child.sessionID})`;
}

/** The note added to a lead's next model request while its subagents still run. */
export function reminderText(running: ChildStatus[]): string | undefined {
  if (running.length === 0) {
    return undefined;
  }
  const count =
    running.length === 1 ? "1 subagent is" : `${running.length} subagents are`;
  return `${count} still running: ${running.map(label).join(", ")}. Call await_agents with their ids before you finish or report.`;
}
