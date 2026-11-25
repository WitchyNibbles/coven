import type { SessionApi, SessionLike } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import { isPlainObject, messageOf, show } from "../join/fields.ts";
import { covenOf, findGm, findOtherGms, findPm } from "./find.ts";
import { GM_PREFIXES, PM_PREFIXES, headPrefix } from "./prefixes.ts";
import { isRoot } from "./role.ts";
import type { Role, RoleInfo, resolveRole } from "./role.ts";
import { sharedState } from "./state.ts";

export interface ToolDeps {
  session: SessionApi;
  resolve: typeof resolveRole;
}

/** A refusal or failure the model reads as the tool's error; nothing was done. */
export class ToolError extends Error {
  override name = "ToolError";
}

const NO_GM = "no GM session; end the turn with the full message";

/** The session's metadata with `coven.role` set, every other entry kept. */
function withRole(session: SessionLike, role: Role): Record<string, unknown> {
  return { ...session.metadata, coven: { ...covenOf(session), role } };
}

async function caller(
  deps: ToolDeps,
  sessionID: string,
): Promise<{ me: SessionLike; role: RoleInfo | undefined }> {
  const get = (id: string): Promise<SessionLike> =>
    deps.session.get({ sessionID: id });
  const me = await get(sessionID);
  return { me, role: await deps.resolve(sessionID, get) };
}

/**
 * Throws unless the caller is the GM: a resolved role of exactly `gm` on a root session. A
 * failed lookup, a PM, a former GM, a child of the GM and a session with no role are refused.
 */
export async function requireGm(
  deps: ToolDeps,
  sessionID: string,
  tool: string,
): Promise<void> {
  const { me, role } = await caller(deps, sessionID);
  if (role?.role !== "gm" || !isRoot(me)) {
    throw new ToolError(`only the GM can use ${tool}`);
  }
}

/** Runs a tool body; whatever it throws, the model reads it as the tool's error. */
export async function asToolError<T>(body: () => Promise<T>): Promise<T> {
  try {
    return await body();
  } catch (error) {
    throw error instanceof ToolError ? error : new ToolError(messageOf(error));
  }
}

export function gmClaimTool(deps: ToolDeps): ToolDefinition {
  return {
    name: "coven_gm_claim",
    description:
      "Marks this session as the GM. Call it first. A session that already holds the GM role " +
      "becomes a former GM, so the newest claim is the one PMs send to. Only a root session " +
      "that is not a PM can claim.",
    input: { type: "object", properties: {}, additionalProperties: false },
    options: { codemode: false },
    execute: async (_raw, context) => {
      const { me, role } = await caller(deps, context.sessionID);
      if (!isRoot(me)) {
        throw new ToolError("only a root session can claim the GM role");
      }
      if (role?.role === "pm") {
        throw new ToolError("a PM cannot claim the GM role");
      }
      // refused callers never hold the flag; no await between the check and the flag, so two
      // simultaneous claims cannot both pass
      const state = sharedState();
      if (state.claiming) {
        throw new ToolError("a claim is already running");
      }
      state.claiming = true;
      try {
        return await takeOver(deps, me, role);
      } finally {
        state.claiming = false;
      }
    },
  };
}

async function takeOver(
  deps: ToolDeps,
  me: SessionLike,
  role: RoleInfo | undefined,
): Promise<{ content: string }> {
  const others = await findOtherGms(deps.session, me.id);
  // every other GM steps down first: a claim that fails after this leaves no GM, never two
  for (const other of others) {
    await deps.session.update({
      sessionID: other.id,
      metadata: withRole(other, "former-gm"),
    });
  }
  const already = role?.role === "gm";
  if (!already) {
    await deps.session.update({
      sessionID: me.id,
      metadata: withRole(me, "gm"),
    });
  }
  const demoted =
    others.length === 0
      ? ""
      : `; ${others.map((other) => other.id).join(", ")} demoted to former GM`;
  return {
    content: `${already ? "this session is already the GM" : "this session is now the GM"}${demoted}`,
  };
}

interface SendInput {
  to?: string;
  text: string;
}

function parseSend(raw: unknown): SendInput {
  if (!isPlainObject(raw)) {
    throw new ToolError(`input must be an object, got ${show(raw)}`);
  }
  const { to, text } = raw;
  if (typeof text !== "string" || text.trim() === "") {
    throw new ToolError(`text must be a non-empty string, got ${show(text)}`);
  }
  if (to !== undefined && typeof to !== "string") {
    throw new ToolError(`to must be a project code string, got ${show(to)}`);
  }
  return to === undefined ? { text } : { to, text };
}

/** Throws unless the message starts with one of the sender's prefixes. */
function checkPrefix(text: string, allowed: readonly string[], who: string) {
  const prefix = headPrefix(text);
  if (prefix === undefined || !allowed.includes(prefix)) {
    throw new ToolError(
      `a ${who} message must start with one of ${allowed.join(", ")}`,
    );
  }
}

interface Route {
  target: SessionLike;
  text: string;
}

async function routeFromGm(deps: ToolDeps, input: SendInput): Promise<Route> {
  checkPrefix(input.text, GM_PREFIXES, "GM");
  if (input.to === undefined) {
    throw new ToolError("to is required: the code of the PM to send to");
  }
  const target = await findPm(deps.session, input.to);
  if (target === undefined) {
    throw new ToolError(`no PM with code "${input.to}"`);
  }
  return { target, text: `[from gm] ${input.text}` };
}

async function routeFromPm(
  deps: ToolDeps,
  input: SendInput,
  code: string | undefined,
): Promise<Route> {
  checkPrefix(input.text, PM_PREFIXES, "PM");
  if (code === undefined) {
    throw new ToolError("this PM session has no code in its metadata");
  }
  const target = await findGm(deps.session);
  if (target === undefined) {
    throw new ToolError(NO_GM);
  }
  return { target, text: `[from pm-${code}] ${input.text}` };
}

export function sendTool(deps: ToolDeps): ToolDefinition {
  return {
    name: "coven_send",
    description:
      "Sends a message between the GM and a PM. The first word of the first line is the prefix: " +
      `the GM sends ${GM_PREFIXES.join(", ")} to the PM named by to (its project code); ` +
      `a PM sends ${PM_PREFIXES.join(", ")} to the GM and to is ignored. ` +
      "The message is queued for the receiver's next turn.",
    input: {
      type: "object",
      properties: {
        to: { type: "string", minLength: 1 },
        text: { type: "string", minLength: 1 },
      },
      required: ["text"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: async (raw, context) => {
      const input = parseSend(raw);
      const { me, role } = await caller(deps, context.sessionID);
      if (role === undefined) {
        throw new ToolError(
          "this session has no role: only the GM or a PM can send",
        );
      }
      if (role.role === "former-gm") {
        throw new ToolError("a former GM cannot send: only the GM or a PM can");
      }
      if (!isRoot(me)) {
        throw new ToolError("only a root session can send, not a child of one");
      }
      const route =
        role.role === "gm"
          ? await routeFromGm(deps, input)
          : await routeFromPm(deps, input, role.code);
      await deps.session.prompt({
        sessionID: route.target.id,
        text: route.text,
        delivery: "queue",
      });
      if (role.role === "pm") {
        sharedState().messaged.add(context.sessionID);
      }
      return {
        content:
          role.role === "gm"
            ? `sent to pm-${input.to ?? ""}`
            : "sent to the GM",
      };
    },
  };
}
