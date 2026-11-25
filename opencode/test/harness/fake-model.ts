import { createServer } from "node:http";
import type { Server, ServerResponse } from "node:http";

import type { ChatMessage, Reply, ToolCall } from "./chat.ts";

/** The address the project config points at; the fake listens on loopback only. */
export const FAKE_PORT = 49999;
const LOOPBACK = "127.0.0.1";
const USAGE = { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 };

/**
 * `plain` answers a tool call with a proper `tool_calls` reply. `litellm` strands the call in
 * the text when the request has tools and no `tool_choice`, as a LiteLLM gateway in front of
 * vLLM does (OpenCode issue 60202).
 */
export type FakeMode = "plain" | "litellm";

export type Decide = (
  messages: readonly ChatMessage[],
  toolNames: readonly string[],
) => Reply;

/** What one chat request looked like, for tests that check what OpenCode sent. */
export interface RecordedRequest {
  tools: number;
  hasToolChoice: boolean;
  toolChoice: unknown;
  stream: boolean;
  /** The Authorization header the request carried, or undefined when it had none. */
  authorization: string | undefined;
}

export interface FakeModel {
  port: number;
  requests(): readonly RecordedRequest[];
  close(): Promise<void>;
}

export interface FakeOptions {
  decide: Decide;
  mode?: FakeMode;
  /** The port to listen on; 0 picks a free one. Defaults to FAKE_PORT. */
  port?: number;
}

type Stranded = { kind: "stranded"; text: string };

interface ChatRequest {
  messages?: ChatMessage[];
  tools?: { function?: { name?: string } }[];
  tool_choice?: unknown;
  stream?: boolean;
}

function parseRequest(raw: string): ChatRequest {
  try {
    const parsed: unknown = JSON.parse(raw || "{}");
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

/** The text a gateway leaves in `content` when it does not translate the model's tool calls. */
export function strandedText(calls: readonly ToolCall[]): string {
  return calls
    .map(
      (call) =>
        `<tool_call>${JSON.stringify({ name: call.name, arguments: call.args })}</tool_call>`,
    )
    .join("\n");
}

/** The reply for one chat request body, and a record of the request; it opens no socket. */
export function replyFor(
  raw: string,
  mode: FakeMode,
  decide: Decide,
  authorization?: string,
): { reply: Reply | Stranded; recorded: RecordedRequest } {
  const request = parseRequest(raw);
  const tools = request.tools ?? [];
  const names = tools.flatMap((tool) =>
    tool.function?.name === undefined ? [] : [tool.function.name],
  );
  const hasToolChoice = "tool_choice" in request;
  const recorded: RecordedRequest = {
    tools: tools.length,
    hasToolChoice,
    toolChoice: request.tool_choice,
    stream: request.stream !== false,
    authorization,
  };
  const reply = decide(request.messages ?? [], names);
  const strand =
    mode === "litellm" &&
    tools.length > 0 &&
    !hasToolChoice &&
    reply.kind === "calls";
  return {
    reply: strand
      ? { kind: "stranded", text: strandedText(reply.calls) }
      : reply,
    recorded,
  };
}

function toolCalls(reply: Extract<Reply, { kind: "calls" }>, tag: string) {
  return reply.calls.map((call, index) => ({
    index,
    id: `call_${tag}_${index}`,
    type: "function",
    function: { name: call.name, arguments: JSON.stringify(call.args) },
  }));
}

function chunk(res: ServerResponse, delta: object, finish: string | null) {
  const body = {
    id: "fake",
    object: "chat.completion.chunk",
    created: 0,
    model: "fake",
    choices: [{ index: 0, delta, finish_reason: finish }],
    ...(finish === null ? {} : { usage: USAGE }),
  };
  res.write(`data: ${JSON.stringify(body)}\n\n`);
}

function stream(res: ServerResponse, reply: Reply, tag: string): void {
  res.writeHead(200, { "content-type": "text/event-stream" });
  if (reply.kind === "calls") {
    chunk(res, { role: "assistant", tool_calls: toolCalls(reply, tag) }, null);
    chunk(res, {}, "tool_calls");
  } else {
    chunk(res, { role: "assistant", content: reply.text }, null);
    chunk(res, {}, "stop");
  }
  res.end("data: [DONE]\n\n");
}

function whole(res: ServerResponse, reply: Reply, tag: string): void {
  const message =
    reply.kind === "calls"
      ? { role: "assistant", content: null, tool_calls: toolCalls(reply, tag) }
      : { role: "assistant", content: reply.text };
  const finish = reply.kind === "calls" ? "tool_calls" : "stop";
  res.writeHead(200, { "content-type": "application/json" });
  res.end(
    JSON.stringify({
      id: "fake",
      object: "chat.completion",
      created: 0,
      model: "fake",
      choices: [{ index: 0, message, finish_reason: finish }],
      usage: USAGE,
    }),
  );
}

function modelList(res: ServerResponse): void {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(
    JSON.stringify({ object: "list", data: [{ id: "fake", object: "model" }] }),
  );
}

function answer(
  url: string,
  raw: string,
  authorization: string | undefined,
  res: ServerResponse,
  context: {
    tag: string;
    mode: FakeMode;
    decide: Decide;
    log: RecordedRequest[];
  },
): void {
  if (!url.includes("chat/completions")) {
    modelList(res);
    return;
  }
  const { reply, recorded } = replyFor(
    raw,
    context.mode,
    context.decide,
    authorization,
  );
  context.log.push(recorded);
  const text: Reply =
    reply.kind === "stranded" ? { kind: "text", text: reply.text } : reply;
  (recorded.stream ? stream : whole)(res, text, context.tag);
}

/** Starts the scripted model on loopback; the caller closes it. */
export function startFakeModel(options: FakeOptions): Promise<FakeModel> {
  const mode = options.mode ?? "plain";
  const log: RecordedRequest[] = [];
  let counter = 0;
  const server: Server = createServer((req, res) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (part: string) => {
      raw += part;
    });
    req.on("end", () => {
      counter += 1;
      const tag = String(counter);
      answer(req.url ?? "", raw, req.headers.authorization, res, {
        tag,
        mode,
        decide: options.decide,
        log,
      });
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? FAKE_PORT, LOOPBACK, () => {
      const address = server.address();
      resolve({
        port:
          typeof address === "object" && address !== null ? address.port : 0,
        requests: () => log,
        close: () =>
          new Promise<void>((done) => {
            server.closeAllConnections();
            server.close(() => {
              done();
            });
          }),
      });
    });
  });
}
