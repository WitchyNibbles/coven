import type { SessionLike } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import {
  isPlainObject,
  messageOf,
  nonEmptyString,
  show,
} from "../join/fields.ts";
import { busyIds, listAll } from "./busy.ts";
import { findPm, pmRootsIn } from "./find.ts";
import type { PmRoot } from "./find.ts";
import { sharedState } from "./state.ts";
import { ToolError, asToolError, requireGm } from "./tools.ts";
import type { ToolDeps } from "./tools.ts";

const MINUTE_MS = 60_000;
/** A child busy this long shows in the list, so the GM can see a PM that has gone quiet. */
const CHILD_BUSY_SHOWN_MS = 30 * MINUTE_MS;
const HEAD_LIMIT = 200;

/**
 * What the list needs beyond `ctx.session`: the server's own view of who is working, what waits
 * on an ask and what a PM last said. It reads the server on each call (`serverStatus`, wired by
 * the plugin setup); the plugin keeps no record of its own.
 *
 * Every method rejects when the server cannot be read; a resolved value means known, so
 * "nothing busy" and "no asks" are never what a failed read looks like.
 */
export interface PmStatus {
  /** What one read of the session's tree shows: its waiting asks and its longest-busy child. */
  watch(sessionID: string): Promise<{
    /** Request ids of the asks waiting in the session's tree: always a bug for a PM. */
    asks: readonly string[];
    /** How long the longest-busy child has been busy, or undefined with no busy child. */
    childBusyMs: number | undefined;
  }>;
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

const NOT_FOUND_TEXT = /^session not found$/i;

const NOT_FOUND_SUFFIX = "NotFoundError";

/**
 * Whether the error's `name` or `_tag` is a session's not-found: exactly `NotFoundError`, or one
 * ending in it that is a session's, by the `Session.` prefix (2.3.2's plugin side throws
 * `Session.NotFoundError`) or by the `sessionID` the error carries (its HTTP tag is
 * `SessionNotFoundError`). `Permission.NotFoundError` and the like are another outage.
 */
function namesNotFound(error: Record<string, unknown>): boolean {
  return [error["name"], error["_tag"]].some(
    (label) =>
      label === NOT_FOUND_SUFFIX ||
      (typeof label === "string" &&
        label.endsWith(NOT_FOUND_SUFFIX) &&
        (label.startsWith("Session.") || "sessionID" in error)),
  );
}

/**
 * Whether a `get` error says the session is gone. A numeric `status` or `statusCode` decides
 * alone (404 only). With none, only a session's not-found name or tag (`namesNotFound`) or the
 * exact text "session not found" count; any other error, a failed lookup of a model or host
 * included, is an outage the GM must see.
 */
function isNotFound(error: unknown): boolean {
  if (!isPlainObject(error)) {
    return false;
  }
  const status = error["status"] ?? error["statusCode"];
  if (typeof status === "number") {
    return status === 404;
  }
  return namesNotFound(error) || NOT_FOUND_TEXT.test(String(error["message"]));
}

/** What `get` says about the session, or the line to show when it cannot say. */
async function fetched(
  deps: ToolDeps,
  sessionID: string,
): Promise<SessionLike | "missing" | { unavailable: string }> {
  try {
    return await deps.session.get({ sessionID });
  } catch (error) {
    return isNotFound(error) ? "missing" : { unavailable: messageOf(error) };
  }
}

async function describe(
  deps: ListDeps,
  busy: ReadonlySet<string>,
  pm: PmRoot,
): Promise<string> {
  const { id } = pm.session;
  const who = `pm-${pm.code}  ${id}`;
  const got = await fetched(deps, id);
  if (got === "missing") {
    return `${who}  missing`;
  }
  if ("unavailable" in got) {
    return `${who}  status unavailable: ${got.unavailable}`;
  }
  const parts = [who, busy.has(id) ? "busy" : "idle"];
  const outcome = got.outcome ?? pm.session.outcome;
  if (outcome !== undefined) {
    parts.push(`last ${outcome}`);
  }
  parts.push(...(await watched(deps.status, id)));
  return parts.join("  ");
}

/** The waiting asks, a long-busy child and the last message of one PM, as line parts. */
async function watched(status: PmStatus, id: string): Promise<string[]> {
  const { asks, childBusyMs: child } = await status.watch(id);
  const parts = asks.map((ask) => `ASK WAITING (bug) ${ask}`);
  if (child !== undefined && child >= CHILD_BUSY_SHOWN_MS) {
    parts.push(`child busy ${Math.floor(child / MINUTE_MS)}m`);
  }
  const last = await status.lastMessage(id);
  if (last !== undefined && head(last) !== "") {
    parts.push(`"${head(last)}"`);
  }
  return parts;
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
        // one listing gives both the PM roots and who is working
        const live = await listAll(deps.session, { archived: false });
        const busy = busyIds(live);
        const lines = await Promise.all(
          pmRootsIn(live).map((pm) => describe(deps, busy, pm)),
        );
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

async function livePm(deps: ToolDeps, code: string): Promise<SessionLike> {
  const found = await findPm(deps.session, code);
  if (found === undefined) {
    throw new ToolError(`no PM with code "${code}"`);
  }
  return found;
}

/**
 * Whether the server holds the session archived. `update` answers nothing either way, and 2.3.2's
 * plugin side leaves `archived` alone, so only the session read back says what happened.
 */
async function isArchived(deps: ToolDeps, sessionID: string): Promise<boolean> {
  const read = await deps.session.get({ sessionID });
  return typeof read.time?.archived === "number";
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
        const code = nonEmptyString(input, "code", ToolError);
        const text = nonEmptyString(input, "text", ToolError);
        const live = await findPm(deps.session, code);
        const target = live ?? (await findPm(deps.session, code, true));
        if (target === undefined) {
          throw new ToolError(`no PM with code "${code}"`);
        }
        if (live === undefined) {
          await deps.session.update({ sessionID: target.id, archived: false });
          if (await isArchived(deps, target.id)) {
            throw new ToolError(
              `pm-${code} is still archived: the server did not unarchive it, so nothing was sent`,
            );
          }
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
      "archive true, archives the session and reads it back; an error says when the server did " +
      "not archive it. No turn-end notice follows the stop.",
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
        const code = nonEmptyString(input, "code", ToolError);
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
          if (!(await isArchived(deps, target.id))) {
            throw new ToolError(
              `stopped pm-${code} but the server did not archive it`,
            );
          }
        }
        return {
          content: `stopped pm-${code}${archive ? " and archived it" : ""}`,
        };
      }),
  };
}
