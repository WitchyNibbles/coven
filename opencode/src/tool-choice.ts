import type { HttpRequestEvent } from "./context.ts";
import { isPlainObject } from "./join/fields.ts";

/**
 * Compatibility shim for OpenCode issue #60202: 2.x sends no `tool_choice` to openai-compatible
 * providers on ordinary turns, so a vLLM or LiteLLM gateway can leave the tool call in `content`
 * and the turn ends silently. For the providers the user lists, this adds `tool_choice: "auto"`.
 *
 * The hook fails open. It is not a guard: a body it cannot read or does not understand is sent
 * exactly as OpenCode built it.
 */

export type ToolChoiceResult =
  | { kind: "changed"; body: string }
  | { kind: "unchanged" }
  | { kind: "unparsable" };

const UNCHANGED: ToolChoiceResult = { kind: "unchanged" };

function parseJson(body: string): unknown {
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return undefined;
  }
}

function wantsAuto(payload: Record<string, unknown>): boolean {
  return (
    Array.isArray(payload.tools) &&
    payload.tools.length > 0 &&
    !Object.hasOwn(payload, "tool_choice")
  );
}

/** The body with `tool_choice: "auto"` added, only when it has tools and no `tool_choice` key. */
export function addToolChoice(body: string): ToolChoiceResult {
  const payload = parseJson(body);
  if (payload === undefined) {
    return { kind: "unparsable" };
  }
  if (!isPlainObject(payload) || !wantsAuto(payload)) {
    return UNCHANGED;
  }
  // A non-empty object ending in "}": insert a member instead of re-serialising, so numbers keep their bytes.
  return {
    kind: "changed",
    body: `${body.trimEnd().slice(0, -1)},"tool_choice":"auto"}`,
  };
}

function withBody(request: Request, body: string): Request {
  const headers = new Headers(request.headers);
  headers.delete("content-length");
  return new Request(request.url, {
    method: request.method,
    headers,
    signal: request.signal,
    body,
  });
}

/**
 * The `http.request` hook for a provider. `log` hears each session's first body it could not use;
 * hooks that share `warned` share that once-per-session rule.
 */
export function toolChoiceHook(
  log: (line: string) => void,
  warned: Set<string> = new Set(),
): (event: HttpRequestEvent) => Promise<void> {
  const warnOnce = (sessionID: string, problem: string): void => {
    if (!warned.has(sessionID)) {
      warned.add(sessionID);
      log(
        `coven: tool_choice: ${problem}; sent unchanged (session ${sessionID})`,
      );
    }
  };
  return async (event) => {
    try {
      const text = await event.request.clone().text();
      if (text === "") {
        return;
      }
      const result = addToolChoice(text);
      if (result.kind === "changed") {
        event.request = withBody(event.request, result.body);
      } else if (result.kind === "unparsable") {
        warnOnce(event.sessionID, "request body is not JSON");
      }
    } catch {
      warnOnce(event.sessionID, "request body could not be read");
    }
  };
}
