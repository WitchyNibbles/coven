import type { SessionLike } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import { isPlainObject, show } from "../join/fields.ts";
import { findPm, listPms } from "./find.ts";
import { sharedState } from "./state.ts";
import { ToolError, asToolError, requireGm } from "./tools.ts";
import type { ToolDeps } from "./tools.ts";

const MINUTE_MS = 60_000;
/** A child busy this long shows in the list, so the GM can see a PM that has gone quiet. */
const CHILD_BUSY_SHOWN_MS = 30 * MINUTE_MS;
const HEAD_LIMIT = 200;

/**
 * What the list needs beyond `ctx.session`: the server's own view of who is working and the
 * plugin's record of the PMs' children. The plugin setup wires it to the real thing.
 */
export interface PmStatus {
  /** Ids of the sessions with a turn running. */
  active(): Promise<ReadonlySet<string>>;
  /** Request ids of the asks waiting in the session's tree: always a bug for a PM. */
  asks(sessionID: string): Promise<readonly string[]>;
  /** How long the PM's longest-busy child has been busy, or undefined with no busy child. */
  childBusyMs(sessionID: string): Promise<number | undefined>;
  /** The text of the session's last message, or undefined with none. */
  lastMessage(sessionID: string): Promise<string | undefined>;
}

export interface ListDeps extends ToolDeps {
  status: PmStatus;
}

/** The first line of the text, cut to `HEAD_LIMIT` characters. */
function head(message: string): string {
  const first = (message.split("\n")[0] ?? "").trim();
  return first.length > HEAD_LIMIT
    ? `${first.slice(0, HEAD_LIMIT - 1)}…`
    : first;
}

async function exists(deps: ToolDeps, sessionID: string): Promise<boolean> {
  try {
    await deps.session.get({ sessionID });
    return true;
  } catch {
    return false;
  }
}

async function describe(
  deps: ListDeps,
  busy: ReadonlySet<string>,
  pm: { session: SessionLike; code: string },
): Promise<string> {
  const { id, outcome } = pm.session;
  const who = `pm-${pm.code}  ${id}`;
  if (!(await exists(deps, id))) {
    return `${who}  missing`;
  }
  const parts = [who, busy.has(id) ? "busy" : "idle"];
  if (outcome !== undefined) {
    parts.push(`last ${outcome}`);
  }
  for (const ask of await deps.status.asks(id)) {
    parts.push(`ASK WAITING (bug) ${ask}`);
  }
  const child = await deps.status.childBusyMs(id);
  if (child !== undefined && child >= CHILD_BUSY_SHOWN_MS) {
    parts.push(`child busy ${Math.floor(child / MINUTE_MS)}m`);
  }
  const last = await deps.status.lastMessage(id);
  if (last !== undefined && head(last) !== "") {
    parts.push(`"${head(last)}"`);
  }
  return parts.join("  ");
}

export function pmListTool(deps: ListDeps): ToolDefinition {
  return {
    name: "coven_pm_list",
    description:
      "Lists the live PM sessions, one line each: code, session id, busy or idle, the last " +
      "outcome, any waiting ask (ASK WAITING (bug) with its request id), a child busy for " +
      "30 minutes or more, and the head of the last message. A PM whose session is gone " +
      "shows as missing.",
    input: { type: "object", properties: {}, additionalProperties: false },
    options: { codemode: false },
    execute: (_raw, context) =>
      asToolError(async () => {
        await requireGm(deps, context.sessionID, "coven_pm_list");
        const pms = await listPms(deps.session);
        const busy = await deps.status.active();
        const lines = [];
        for (const pm of pms) {
          lines.push(await describe(deps, busy, pm));
        }
        return {
          content: lines.length === 0 ? "no PM sessions" : lines.join("\n"),
        };
      }),
  };
}

function object(raw: unknown): Record<string, unknown> {
  if (!isPlainObject(raw)) {
    throw new ToolError(`input must be an object, got ${show(raw)}`);
  }
  return raw;
}

function nonEmpty(raw: Record<string, unknown>, key: string): string {
  const value = raw[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new ToolError(
      `${key} must be a non-empty string, got ${show(value)}`,
    );
  }
  return value;
}

async function livePm(deps: ToolDeps, code: string): Promise<SessionLike> {
  const found = await findPm(deps.session, code);
  if (found === undefined) {
    throw new ToolError(`no PM with code "${code}"`);
  }
  return found;
}

export function pmResumeTool(deps: ToolDeps): ToolDefinition {
  return {
    name: "coven_pm_resume",
    description:
      "Resumes a PM by its code: unarchives it if needed and queues it a turn that starts " +
      "with RESUMED by gm, then your text. Use it after a stop or a server restart.",
    input: {
      type: "object",
      properties: {
        code: { type: "string", minLength: 1 },
        text: { type: "string", minLength: 1 },
      },
      required: ["code", "text"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: (raw, context) =>
      asToolError(async () => {
        await requireGm(deps, context.sessionID, "coven_pm_resume");
        const input = object(raw);
        const code = nonEmpty(input, "code");
        const text = nonEmpty(input, "text");
        const live = await findPm(deps.session, code);
        const target = live ?? (await findPm(deps.session, code, true));
        if (target === undefined) {
          throw new ToolError(`no PM with code "${code}"`);
        }
        if (live === undefined) {
          await deps.session.update({ sessionID: target.id, archived: false });
        }
        sharedState().stopped.delete(target.id);
        await deps.session.prompt({
          sessionID: target.id,
          text: `RESUMED by gm\n${text}`,
          delivery: "queue",
        });
        return { content: `resumed pm-${code}` };
      }),
  };
}

function archiveFlag(input: Record<string, unknown>): boolean {
  const value = input["archive"];
  if (value !== undefined && typeof value !== "boolean") {
    throw new ToolError(`archive must be true or false, got ${show(value)}`);
  }
  return value === true;
}

export function pmStopTool(deps: ToolDeps): ToolDefinition {
  return {
    name: "coven_pm_stop",
    description:
      "Stops a PM by its code: interrupts its turn (which also ends a waiting ask) and, with " +
      "archive true, archives the session. No turn-end notice follows the stop.",
    input: {
      type: "object",
      properties: {
        code: { type: "string", minLength: 1 },
        archive: { type: "boolean" },
      },
      required: ["code"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: (raw, context) =>
      asToolError(async () => {
        await requireGm(deps, context.sessionID, "coven_pm_stop");
        const input = object(raw);
        const code = nonEmpty(input, "code");
        const archive = archiveFlag(input);
        const target = await livePm(deps, code);
        const { stopped } = sharedState();
        // marked first: the end event of the interrupted turn must find the mark
        stopped.add(target.id);
        try {
          await deps.session.interrupt({ sessionID: target.id });
        } catch (error) {
          stopped.delete(target.id);
          throw error;
        }
        if (archive) {
          await deps.session.update({ sessionID: target.id, archived: true });
        }
        return {
          content: `stopped pm-${code}${archive ? " and archived it" : ""}`,
        };
      }),
  };
}
