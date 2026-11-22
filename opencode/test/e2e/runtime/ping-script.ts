/**
 * The script of the fake model for the runtime suite: it asks for `coven_ping` once, then
 * reports what the tool returned. A request with no tools is the title agent's.
 */
import { textOf } from "../../harness/chat.ts";
import type { ChatMessage, Reply } from "../../harness/chat.ts";
import type { Decide } from "../../harness/fake-model.ts";

const PING = "coven_ping";

/** The text of the tool results that answer a call to `name`. */
function resultsOf(messages: readonly ChatMessage[], name: string): string[] {
  const ids = new Set(
    messages
      .flatMap((message) => message.tool_calls ?? [])
      .filter((call) => call.function?.name === name)
      .map((call) => call.id),
  );
  return messages
    .filter(
      (message) =>
        message.role === "tool" && ids.has(message.tool_call_id ?? ""),
    )
    .map((message) => textOf(message.content));
}

export const decidePing: Decide = (messages, toolNames): Reply => {
  if (toolNames.length === 0) {
    return { kind: "text", text: "title" };
  }
  if (!toolNames.includes(PING)) {
    return { kind: "text", text: "NO PING TOOL" };
  }
  const [result] = resultsOf(messages, PING);
  return result === undefined
    ? { kind: "calls", calls: [{ name: PING, args: {} }] }
    : { kind: "text", text: `PING DONE ${result}` };
};
