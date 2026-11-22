/** The chat shapes the fake model reads and answers with. */

export interface ChatMessage {
  role: string;
  content?: unknown;
  tool_calls?: readonly { id: string; function?: { name?: string } }[];
  tool_call_id?: string;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
}

export type Reply =
  { kind: "text"; text: string } | { kind: "calls"; calls: ToolCall[] };

/** The text of a message content: a string, or the text parts of a list. */
export function textOf(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return "";
  }
  return content
    .map((part: unknown) => {
      if (typeof part === "string") {
        return part;
      }
      const text = (part as { text?: unknown } | null)?.text;
      return typeof text === "string" ? text : "";
    })
    .join("\n");
}
