/**
 * A plugin the hierarchy e2e adds to the scratch profile, loaded once per location like coven's
 * own. It writes one JSON line each to the file named by the `out` option, with the location it
 * was loaded for as `copy`: every `execute.before` (tool and session), every shell
 * `create.before` (the command and the `cwd` string it carries) and every turn-end event (type
 * and session). From the lines the e2e learns which plugin copies see which event. No value that
 * could be a secret is written: shell commands are the e2e's own.
 */
import { appendFileSync } from "node:fs";

interface Event {
  type: string;
  data: { sessionID?: string };
}

interface ProbeContext {
  options?: { out?: unknown };
  location: { directory: string };
  tool: {
    transform(
      callback: (editor: { add(tool: ProbeTool): void }) => void,
    ): Promise<unknown>;
    hook(
      name: "execute.before",
      callback: (event: { tool: string; sessionID: string }) => unknown,
    ): Promise<unknown>;
  };
  shell: {
    hook(
      name: "create.before",
      callback: (event: { command: string; cwd: string }) => unknown,
    ): Promise<unknown>;
  };
  event: {
    subscribe(options: {
      types: string[];
      signal: AbortSignal;
    }): AsyncIterable<Event>;
  };
  session: {
    get(input: { sessionID: string }): Promise<unknown>;
    list(input: { archived?: boolean }): Promise<{ data: { id: string }[] }>;
    update(input: {
      sessionID: string;
      archived?: boolean | number;
    }): Promise<unknown>;
  };
}

interface ProbeTool {
  name: string;
  description: string;
  input: Record<string, unknown>;
  options: { codemode: false };
  execute(input: unknown): Promise<{ content: string }>;
}

/** The shape of whatever `session.get` throws: names and numbers only, the message cut short. */
function shapeOf(error: unknown): Record<string, unknown> {
  const fields = (error ?? {}) as Record<string, unknown>;
  const cause = fields["cause"] as Record<string, unknown> | undefined;
  return {
    name: fields["name"],
    tag: fields["_tag"],
    status: fields["status"],
    statusCode: fields["statusCode"],
    message: String(fields["message"]).slice(0, 120),
    keys: Object.keys(fields).sort(),
    causeName: cause?.["name"],
    causeTag: cause?.["_tag"],
  };
}

/** `probe_list`: `session.list` with `archived` as given (true, false or left out), answering with the ids that came back. */
function probeList(ctx: ProbeContext): ProbeTool {
  return {
    name: "probe_list",
    description:
      "Lists sessions with the archived filter as given and says which came back.",
    input: {
      type: "object",
      properties: {
        archived: { type: "string", enum: ["true", "false", "omit"] },
      },
      required: ["archived"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: async (input) => {
      const { archived } = input as { archived: string };
      const query =
        archived === "omit" ? {} : { archived: archived === "true" };
      const page = await ctx.session.list(query);
      return {
        content: JSON.stringify({ ids: page.data.map((item) => item.id) }),
      };
    },
  };
}

/** `probe_archive`: `session.update` with `archived` set as given, answering with what it returned. */
function probeArchive(ctx: ProbeContext): ProbeTool {
  return {
    name: "probe_archive",
    description:
      "Sets a session's archived flag through the plugin API and says what came back.",
    input: {
      type: "object",
      properties: {
        sessionID: { type: "string", minLength: 1 },
        archived: { type: ["boolean", "number"] },
      },
      required: ["sessionID", "archived"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: async (input) => {
      const { sessionID, archived } = input as {
        sessionID: string;
        archived: boolean | number;
      };
      const answer = await ctx.session.update({ sessionID, archived });
      return {
        content: JSON.stringify({
          answered: answer === undefined ? "undefined" : answer,
        }),
      };
    },
  };
}

/** `probe_get`: `session.get` on an id, answering with what it returned or the shape of its error. */
function probeGet(ctx: ProbeContext): ProbeTool {
  return {
    name: "probe_get",
    description:
      "Reads a session and says whether it was found or how the read failed.",
    input: {
      type: "object",
      properties: { sessionID: { type: "string", minLength: 1 } },
      required: ["sessionID"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: async (input) => {
      const { sessionID } = input as { sessionID: string };
      try {
        await ctx.session.get({ sessionID });
        return { content: JSON.stringify({ found: true }) };
      } catch (error) {
        return { content: JSON.stringify({ found: false, ...shapeOf(error) }) };
      }
    },
  };
}

const ENDS = [
  "session.execution.started",
  "session.execution.succeeded",
  "session.execution.failed",
  "session.execution.interrupted",
];

export default {
  id: "hierarchy-probe",
  setup: (ctx: ProbeContext): (() => void) => {
    const out = ctx.options?.out;
    if (typeof out !== "string") {
      throw new Error("hierarchy probe: the out option is missing");
    }
    const copy = ctx.location.directory;
    const record = (line: Record<string, unknown>): void => {
      appendFileSync(out, `${JSON.stringify({ copy, ...line })}\n`);
    };
    const stop = new AbortController();
    ctx.tool
      .transform((editor) => {
        editor.add(probeGet(ctx));
        editor.add(probeList(ctx));
        editor.add(probeArchive(ctx));
      })
      .catch((error: unknown) => {
        record({ kind: "error", error: String(error) });
      });
    ctx.tool
      .hook("execute.before", (event) => {
        record({
          kind: "execute",
          tool: event.tool,
          sessionID: event.sessionID,
        });
      })
      .catch((error: unknown) => {
        record({ kind: "error", error: String(error) });
      });
    ctx.shell
      .hook("create.before", (event) => {
        record({
          kind: "create",
          command: event.command.slice(0, 80),
          cwd: event.cwd,
        });
      })
      .catch((error: unknown) => {
        record({ kind: "error", error: String(error) });
      });
    void (async () => {
      const events = ctx.event.subscribe({ types: ENDS, signal: stop.signal });
      for await (const item of events) {
        record({
          kind: "event",
          type: item.type,
          sessionID: item.data.sessionID ?? "",
        });
      }
    })();
    return () => {
      stop.abort();
    };
  },
};
