/**
 * The script of the fake model for the hierarchy suite. A user message holds directives, one per
 * line: `DO <tool> <json arguments>`. The model makes the directives' tool calls one per turn, in
 * order, then ends the turn with `DONE <n>`. A user message with no directive (a queued message
 * from the other role, a notice) is answered with `ACK`. A request with no tools is the title
 * agent's. Nothing else in a message matters, so the suite decides what each session does by
 * what it sends it.
 */
import { textOf } from "../../harness/chat.ts";
import type { ChatMessage, Reply, ToolCall } from "../../harness/chat.ts";
import type { Decide } from "../../harness/fake-model.ts";

const DIRECTIVE = /^DO (\w+) (\{.*\})\s*$/;

/** The directives of one message text, in order; a line that is not valid JSON is skipped. */
export function directivesOf(text: string): ToolCall[] {
  return text.split("\n").flatMap((line) => {
    const found = DIRECTIVE.exec(line.trim());
    if (found === null) {
      return [];
    }
    try {
      const args = JSON.parse(found[2] ?? "{}") as Record<string, unknown>;
      return [{ name: found[1] ?? "", args }];
    } catch {
      return [];
    }
  });
}

/** `DO <tool> <json>`: the line a session is sent for one call. */
export function doLine(tool: string, args: Record<string, unknown>): string {
  return `DO ${tool} ${JSON.stringify(args)}`;
}

/** A shell call's arguments: the tool wants a description too. */
function complete(call: ToolCall): ToolCall {
  return call.name === "shell" && call.args["description"] === undefined
    ? { name: call.name, args: { ...call.args, description: "run" } }
    : call;
}

/** The reply of the world's fake model. */
export const decideHierarchy: Decide = (
  messages: readonly ChatMessage[],
  toolNames: readonly string[],
): Reply => {
  if (toolNames.length === 0) {
    return { kind: "text", text: "title" };
  }
  const lastUser = messages.map((m) => m.role).lastIndexOf("user");
  const text = textOf(messages[lastUser]?.content);
  const calls = directivesOf(text);
  if (calls.length === 0) {
    return { kind: "text", text: "ACK" };
  }
  const done = messages
    .slice(lastUser + 1)
    .filter((message) => message.role === "tool").length;
  const next = calls[done];
  return next === undefined
    ? { kind: "text", text: `DONE ${done}` }
    : { kind: "calls", calls: [complete(next)] };
};
