import type { SessionApi, SessionLike } from "../context.ts";
import { isPlainObject, messageOf, ownString } from "../join/fields.ts";
import { descendants, isBusy } from "./busy.ts";
import { findGm } from "./find.ts";
import { isRoot, resolveRole } from "./role.ts";
import type { SharedState } from "./state.ts";

const LAST_LINE_LIMIT = 200;
const STARTED = "session.execution.started";

type Outcome = "succeeded" | "failed" | "interrupted";

const OUTCOMES: Readonly<Record<string, Outcome>> = {
  "session.execution.succeeded": "succeeded",
  "session.execution.failed": "failed",
  "session.execution.interrupted": "interrupted",
};

export interface NoticeDeps {
  session: SessionApi;
  state: Pick<SharedState, "messaged" | "stopped">;
  /** The text of the PM's last assistant message; empty when it has none. */
  lastText: (sessionID: string) => Promise<string>;
  /** Epoch ms the plugin started: a child busy since before then is a turn a restart cut off. */
  startedAt: number;
  /** Where a notice that cannot be delivered goes: the serve folder's `log`. */
  log: (line: string) => void;
}

/** Who a session belongs to, from this copy's side. */
type Owner =
  { kind: "pm"; code: string } | { kind: "other" } | { kind: "unreadable" };

/** The marks of a turn, read when its end event arrives, before anything can clear them. */
interface Marks {
  messaged: boolean;
  stopped: boolean;
}

/** The last non-empty line of `text`, cut to `LAST_LINE_LIMIT` characters. */
function lastLine(text: string): string {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
  return (lines.at(-1) ?? "").slice(0, LAST_LINE_LIMIT);
}

/** The session id of a raw event, or `undefined` when the event has none. */
function sessionOf(event: unknown): string | undefined {
  const data = isPlainObject(event) ? event["data"] : undefined;
  return ownString(isPlainObject(data) ? data : undefined, "sessionID");
}

/**
 * Queues a `[coven notice]` into the GM when a PM's turn ends without a message from it (D4).
 * One instance per plugin location: it acts only on PM sessions that work in `directory`, so one
 * notice goes out per turn however many locations see the event.
 */
export class NoticeTracker {
  private readonly directory: string;
  private readonly deps: NoticeDeps;

  constructor(directory: string, deps: NoticeDeps) {
    this.directory = directory;
    this.deps = deps;
  }

  async onEvent(event: unknown): Promise<void> {
    const sessionID = sessionOf(event);
    const type = isPlainObject(event) ? ownString(event, "type") : undefined;
    if (sessionID === undefined || type === undefined) {
      return;
    }
    const { messaged, stopped } = this.deps.state;
    if (type === STARTED) {
      // a mark outlives its turn when no end event followed it: the stop of an idle PM. Only
      // the copy that owns the PM clears: another location's copy could do it before the owner
      // has read the marks of the turn that just ended. A lookup that fails clears too: a stale
      // mark would mute the next turn's notice, and a notice is the safer error.
      if (messaged.has(sessionID) || stopped.has(sessionID)) {
        if ((await this.owner(sessionID)).kind !== "other") {
          messaged.delete(sessionID);
          stopped.delete(sessionID);
        }
      }
      return;
    }
    const outcome = Object.hasOwn(OUTCOMES, type) ? OUTCOMES[type] : undefined;
    if (outcome !== undefined) {
      // no await before the marks are read: the next turn's start event may clear them
      const marks = {
        messaged: messaged.has(sessionID),
        stopped: stopped.has(sessionID),
      };
      await this.ended(sessionID, outcome, marks);
    }
  }

  private async ended(
    sessionID: string,
    outcome: Outcome,
    marks: Marks,
  ): Promise<void> {
    const code = await this.pmCode(sessionID);
    if (code === undefined || marks.stopped) {
      return;
    }
    // a failure is always news; anything else is quiet after a message or while a child works
    if (outcome !== "failed" && (await this.quiet(sessionID, code, marks))) {
      return;
    }
    const how =
      outcome === "failed" && marks.messaged
        ? "after its message"
        : "without a message";
    const last = await this.deps.lastText(sessionID).then(
      (text) => lastLine(text) || "(no text)",
      () => "(unreadable)",
    );
    await this.deliver(
      `[coven notice] pm-${code} ended a turn (${outcome}) ${how}. Last line: ${last}`,
    );
  }

  /** The code of the PM in `directory` this session is, or `undefined` for any other session. */
  private async pmCode(sessionID: string): Promise<string | undefined> {
    const owner = await this.owner(sessionID);
    return owner.kind === "pm" ? owner.code : undefined;
  }

  /** Whether this copy owns the session; a lookup that fails is its own answer, and is logged. */
  private async owner(sessionID: string): Promise<Owner> {
    let session;
    try {
      session = await this.deps.session.get({ sessionID });
    } catch (error) {
      this.deps.log(
        `coven notice: cannot read session ${sessionID}: ${messageOf(error)}`,
      );
      return { kind: "unreadable" };
    }
    if (!isRoot(session) || session.location?.directory !== this.directory) {
      return { kind: "other" };
    }
    const role = await resolveRole(sessionID, () => Promise.resolve(session));
    return role?.role === "pm" && role.code !== undefined
      ? { kind: "pm", code: role.code }
      : { kind: "other" };
  }

  /** True after a message, or while a child works (said in the serve log, so it can be found). */
  private async quiet(
    sessionID: string,
    code: string,
    marks: Marks,
  ): Promise<boolean> {
    if (marks.messaged) {
      return true;
    }
    const child = await this.busyChild(sessionID, code);
    if (child !== undefined) {
      this.deps.log(
        `coven notice: held back for pm-${code}: child ${child.id} is busy`,
      );
    }
    return child !== undefined;
  }

  /** A session below the PM that works. A tree that cannot be read counts as none: the notice goes out. */
  private async busyChild(
    sessionID: string,
    code: string,
  ): Promise<SessionLike | undefined> {
    try {
      const tree = await descendants(this.deps.session, sessionID);
      return tree.find((child) => isBusy(child, this.deps.startedAt));
    } catch (error) {
      this.deps.log(
        `coven notice: cannot read the children of pm-${code}: ${messageOf(error)}`,
      );
      return undefined;
    }
  }

  private async deliver(text: string): Promise<void> {
    try {
      const gm = await findGm(this.deps.session);
      if (gm === undefined) {
        this.deps.log(`${text} (not delivered: no GM session)`);
        return;
      }
      await this.deps.session.prompt({
        sessionID: gm.id,
        text,
        delivery: "queue",
      });
    } catch (error) {
      this.deps.log(`${text} (not delivered: ${messageOf(error)})`);
    }
  }
}
