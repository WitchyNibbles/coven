/** Reads what a finished run left in the lead's exported history. */

export interface SubagentReport {
  childID: string;
  state: string;
  text: string;
}

export interface AwaitResult {
  mode: string;
  done: { sessionID: string; state: string; report?: string }[];
  pending: string[];
  timedOut: boolean;
  waitedSeconds: number;
}

export interface ToolRun {
  name: string;
  status: string;
  input: unknown;
  output: string;
}

export interface LeadHistory {
  reports: SubagentReport[];
  tools: ToolRun[];
  finalText: string;
}

interface ExportedPart {
  type?: string;
  name?: string;
  text?: string;
  state?: {
    status?: string;
    input?: unknown;
    content?: { text?: string }[];
    error?: unknown;
  };
}

export interface ExportedMessage {
  type?: string;
  agent?: string;
  text?: string;
  metadata?: { childID?: string; state?: string };
  content?: ExportedPart[];
}

function reportOf(message: ExportedMessage): SubagentReport[] {
  const text = message.text ?? "";
  if (message.type !== "synthetic" || !text.startsWith("<subagent")) {
    return [];
  }
  const childID = /sessionID="([^"]*)"/.exec(text)?.[1] ?? "";
  const state = /\sstate="([^"]*)"/.exec(text)?.[1] ?? "";
  return [{ childID, state, text }];
}

function outputOf(part: ExportedPart): string {
  const content = part.state?.content;
  return content === undefined
    ? JSON.stringify(part.state?.error ?? "")
    : content.map((piece) => piece.text ?? "").join("\n");
}

function toolOf(part: ExportedPart): ToolRun[] {
  if (part.type !== "tool") {
    return [];
  }
  return [
    {
      name: part.name ?? "",
      status: part.state?.status ?? "",
      input: part.state?.input,
      output: outputOf(part),
    },
  ];
}

function textOfAssistant(message: ExportedMessage): string {
  return (message.content ?? [])
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "")
    .join("\n");
}

/** The session id in the first JSON line of a `run --format json` output. */
export function sessionOf(out: string): string {
  const first = out.split("\n").find((line) => line.startsWith("{")) ?? "{}";
  const id = (JSON.parse(first) as { sessionID?: string }).sessionID;
  if (id === undefined) {
    throw new Error(`the run printed no session id: ${out.slice(0, 300)}`);
  }
  return id;
}

/** The messages of `exported`, the JSON of `opencode session export`. */
export function exportedMessages(exported: string): ExportedMessage[] {
  return (
    (JSON.parse(exported) as { messages?: ExportedMessage[] }).messages ?? []
  );
}

/** `exported` is the JSON of `opencode session export` for the lead's session. */
export function readHistory(exported: string): LeadHistory {
  const messages = exportedMessages(exported);
  const assistants = messages.filter((m) => m.type === "assistant");
  return {
    reports: messages.flatMap(reportOf),
    tools: assistants.flatMap((m) => (m.content ?? []).flatMap(toolOf)),
    finalText:
      assistants
        .map(textOfAssistant)
        .filter((t) => t !== "")
        .at(-1) ?? "",
  };
}

/** The result of each completed `await_agents` call, in order. */
export function awaitResults(history: LeadHistory): AwaitResult[] {
  return history.tools
    .filter(
      (tool) => tool.name === "await_agents" && tool.status === "completed",
    )
    .map((tool) => JSON.parse(tool.output) as AwaitResult);
}

/** One session id. */
export const SESSION_ID = /ses_[A-Za-z0-9_]+/;

/** Every session id in `text`, in order, repeats kept. */
export function sessionIDsIn(text: string): string[] {
  return text.match(new RegExp(SESSION_ID, "g")) ?? [];
}

/** The session ids the lead's `subagent` calls started, in call order. */
export function childIDs(history: LeadHistory): string[] {
  const ids = history.tools
    .filter((tool) => tool.name === "subagent")
    .flatMap((tool) => sessionIDsIn(tool.output));
  return [...new Set(ids)];
}
