import { createServer } from "node:http";
import type { Server, ServerResponse } from "node:http";

import { decide } from "./script.ts";
import type { ChatMessage, Reply } from "./script.ts";

/** The address the project config points at; the fake listens on loopback only. */
export const FAKE_PORT = 49999;
const LOOPBACK = "127.0.0.1";
const USAGE = { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 };

interface ChatRequest {
  messages?: ChatMessage[];
  tools?: { function?: { name?: string } }[];
  stream?: boolean;
}

export interface FakeModel {
  close(): Promise<void>;
}

function parseRequest(raw: string): ChatRequest {
  try {
    return JSON.parse(raw || "{}") as ChatRequest;
  } catch {
    return {};
  }
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

function answer(url: string, raw: string, res: ServerResponse, tag: string) {
  if (!url.includes("chat/completions")) {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        object: "list",
        data: [{ id: "fake", object: "model" }],
      }),
    );
    return;
  }
  const request = parseRequest(raw);
  const names = (request.tools ?? []).flatMap((tool) =>
    tool.function?.name === undefined ? [] : [tool.function.name],
  );
  const reply = decide(request.messages ?? [], names);
  (request.stream === false ? whole : stream)(res, reply, tag);
}

/** Starts the scripted model on loopback; the caller closes it. */
export function startFakeModel(port: number = FAKE_PORT): Promise<FakeModel> {
  let counter = 0;
  const server: Server = createServer((req, res) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (part: string) => {
      raw += part;
    });
    req.on("end", () => {
      counter += 1;
      answer(req.url ?? "", raw, res, String(counter));
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, LOOPBACK, () => {
      resolve({
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
