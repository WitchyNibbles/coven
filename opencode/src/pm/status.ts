import type { SessionApi } from "../context.ts";
import { isPlainObject } from "../join/fields.ts";
import { descendants, isBusy, listAll } from "./busy.ts";
import type { PmStatus } from "./manage.ts";

export interface StatusDeps {
  session: SessionApi;
  permission: {
    list(input: { sessionID: string }): Promise<readonly unknown[]>;
  };
  /** The text of a session's last assistant message; empty when it has none. */
  lastText: (sessionID: string) => Promise<string>;
  now: () => number;
}

const UNKNOWN_ID = "unknown id";

/** The request id of one waiting ask: the first non-empty string of its `id` and `requestID`. */
function requestId(ask: unknown): string {
  if (!isPlainObject(ask)) {
    return UNKNOWN_ID;
  }
  const named = [ask["id"], ask["requestID"]].find(
    (id) => typeof id === "string" && id !== "",
  );
  return typeof named === "string" ? named : UNKNOWN_ID;
}

/**
 * The PM list's view of the server: the session times and the ask lists 2.3.2 gives, read when
 * asked. Every method rejects when a read fails; a resolved value always means "known".
 */
export function serverStatus(deps: StatusDeps): PmStatus {
  return {
    async active() {
      const live = await listAll(deps.session, { archived: false });
      return new Set(live.filter(isBusy).map((session) => session.id));
    },
    async asks(sessionID) {
      const tree = [sessionID];
      for (const child of await descendants(deps.session, sessionID)) {
        tree.push(child.id);
      }
      const ids: string[] = [];
      for (const id of tree) {
        const waiting = await deps.permission.list({ sessionID: id });
        if (!Array.isArray(waiting)) {
          throw new Error(`the ask list of ${id} is not a list`);
        }
        ids.push(...waiting.map(requestId));
      }
      return ids;
    },
    async childBusyMs(sessionID) {
      const since = (await descendants(deps.session, sessionID))
        .filter(isBusy)
        .flatMap((child) => child.time?.busy ?? []);
      return since.length === 0 ? undefined : deps.now() - Math.min(...since);
    },
    async lastMessage(sessionID) {
      const text = await deps.lastText(sessionID);
      return text === "" ? undefined : text;
    },
  };
}
