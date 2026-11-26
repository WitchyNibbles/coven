import type { SessionLike } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import {
  isPlainObject,
  messageOf,
  nonEmptyString,
  show,
} from "../join/fields.ts";
import { busyIds, listAll } from "./busy.ts";
import { covenOf, findPm, pmRootsIn, withCoven } from "./find.ts";
import type { PmRoot } from "./find.ts";
import { sharedState } from "./state.ts";
import { ToolError, asToolError, requireGm } from "./tools.ts";
import type { ToolDeps } from "./tools.ts";
import type { Swept } from "./trees.ts";

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

/**
 * The `name` or `_tag` values of a session's not-found: a bare `NotFoundError`, 2.3.2's plugin
 * side `Session.NotFoundError`, and the HTTP tag `SessionNotFoundError`. Another subject's
 * (`Permission.`, `Model.`, `Message.NotFoundError`, even one that carries a `sessionID`) is
 * another outage.
 */
const SESSION_NOT_FOUND = new Set([
  "NotFoundError",
  "Session.NotFoundError",
  "SessionNotFoundError",
]);

function namesNotFound(error: Record<string, unknown>): boolean {
  return [error["name"], error["_tag"]].some(
    (label) => typeof label === "string" && SESSION_NOT_FOUND.has(label),
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
  const who = `pm-${pm.code}  ${id}${isHidden(pm.session) ? "  archived" : ""}`;
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

/** Whether `coven_pm_stop` hid the PM from the list (`coven.archived`). */
function isHidden(session: SessionLike): boolean {
  return covenOf(session)["archived"] === true;
}

function object(raw: unknown): Record<string, unknown> {
  if (!isPlainObject(raw)) {
    throw new ToolError(`input must be an object, got ${show(raw)}`);
  }
  return raw;
}

/** An optional true or false input; false when it is missing. */
function flag(input: Record<string, unknown>, name: string): boolean {
  const value = input[name];
  if (value !== undefined && typeof value !== "boolean") {
    throw new ToolError(`${name} must be true or false, got ${show(value)}`);
  }
  return value === true;
}

/** The lines of the PMs archived in OpenCode, which the live listing leaves out. */
async function archivedLines(deps: ToolDeps): Promise<string[]> {
  const archived = await listAll(deps.session, { archived: true });
  return pmRootsIn(archived).map(
    (pm) => `pm-${pm.code}  ${pm.session.id}  archived in OpenCode`,
  );
}

export function pmListTool(deps: ListDeps): ToolDefinition {
  return {
    name: "coven_pm_list",
    description:
      "Lists the live PM sessions, one line each: code, session id, busy or idle, the last " +
      "outcome, any waiting ask (ASK WAITING (bug) with its request id), a child busy for " +
      "30 minutes or more, and the head of the last message. A PM whose session is gone " +
      "shows as missing. A PM hidden by coven_pm_stop with archive true is left out; with " +
      "archived true it is listed, marked archived, and so is a PM archived in OpenCode.",
    input: {
      type: "object",
      properties: { archived: { type: "boolean" } },
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: (raw, context) =>
      asToolError(async () => {
        await requireGm(deps, context.sessionID, "coven_pm_list");
        const all = flag(raw === undefined ? {} : object(raw), "archived");
        // one listing gives both the PM roots and who is working
        const live = await listAll(deps.session, { archived: false });
        const busy = busyIds(live);
        const shown = pmRootsIn(live).filter(
          (pm) => all || !isHidden(pm.session),
        );
        const lines = [
          ...(await Promise.all(shown.map((pm) => describe(deps, busy, pm)))),
          ...(all ? await archivedLines(deps) : []),
        ];
        return {
          content: lines.length === 0 ? "no PM sessions" : lines.join("\n"),
        };
      }),
  };
}

async function livePm(deps: ToolDeps, code: string): Promise<SessionLike> {
  const found = await findPm(deps.session, code);
  if (found === undefined) {
    throw new ToolError(`no PM with code "${code}"`);
  }
  return found;
}

/** The PM `code` stands for, live or hidden; a PM archived in OpenCode is a tool error. */
async function resumable(deps: ToolDeps, code: string): Promise<SessionLike> {
  const live = await findPm(deps.session, code);
  if (live !== undefined) {
    return live;
  }
  if ((await findPm(deps.session, code, true)) !== undefined) {
    throw new ToolError(
      `pm-${code} is archived in OpenCode: unarchive it there first, nothing was sent`,
    );
  }
  throw new ToolError(`no PM with code "${code}"`);
}

export function pmResumeTool(deps: ToolDeps): ToolDefinition {
  return {
    name: "coven_pm_resume",
    description:
      "Resumes a PM by its code: shows it in coven_pm_list again if coven_pm_stop hid it, and " +
      "queues it a turn that starts with RESUMED by gm, then your text. Use it after a stop or " +
      "a server restart. A PM archived in OpenCode is refused: unarchive it there first.",
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
        const target = await resumable(deps, code);
        if (isHidden(target)) {
          // `update` replaces the whole metadata: build it from the session as it is now
          const fresh = await deps.session.get({ sessionID: target.id });
          await deps.session.update({
            sessionID: target.id,
            metadata: withCoven(fresh, { archived: undefined }),
          });
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

/** Kills the isolation trees of a PM's shells; the argument is the PM's session id. */
export type TreeStopper = (sessionID: string) => Promise<Swept>;

export interface StopDeps extends ToolDeps {
  /** Without it (no isolation wired) a stop only interrupts the turn. */
  trees?: TreeStopper;
}

/** What a stop adds to its text about the isolation trees it swept. */
function sweptText({ killed, skipped, running }: Swept): string {
  const stale = skipped > 0 ? `; skipped ${skipped} stale record(s)` : "";
  const alive =
    running > 0 ? `; ${running} tree(s) still running after SIGKILL` : "";
  return `; killed ${killed} isolation tree(s)${stale}${alive}`;
}

/** The trees of the PM, after its turn was interrupted; a failure still says the PM was. */
async function stopTrees(
  deps: StopDeps,
  target: SessionLike,
  code: string,
): Promise<string> {
  if (deps.trees === undefined) {
    return "";
  }
  try {
    return sweptText(await deps.trees(target.id));
  } catch (error) {
    throw new ToolError(
      `interrupted pm-${code} but could not stop its isolation trees: ${messageOf(error)}`,
      { cause: error },
    );
  }
}

export function pmStopTool(deps: StopDeps): ToolDefinition {
  return {
    name: "coven_pm_stop",
    description:
      "Stops a PM by its code: interrupts its turn (which also ends a waiting ask), kills the " +
      "isolated process trees of its shells (also a call that moved to the background) and, with " +
      "archive true, hides it from coven_pm_list (the session is not archived in OpenCode; " +
      "coven_pm_resume shows it again). No turn-end notice follows the stop.",
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
        const archive = flag(input, "archive");
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
        const swept = await stopTrees(deps, target, code);
        if (!archive) {
          return { content: `stopped pm-${code}${swept}` };
        }
        // `update` replaces the whole metadata: build it from the session as it is now
        const fresh = await deps.session.get({ sessionID: target.id });
        await deps.session.update({
          sessionID: target.id,
          metadata: withCoven(fresh, { archived: true }),
        });
        return {
          content: `stopped pm-${code}; hidden from coven_pm_list; the session is not archived in OpenCode${swept}`,
        };
      }),
  };
}
