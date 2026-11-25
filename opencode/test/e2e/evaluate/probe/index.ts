/**
 * A plugin the evaluate e2e adds to the scratch profile. It records, one JSON line each to the
 * file named by the `out` option, what the plugin code of this runtime relies on and the
 * 2.3.2 types do not say: the field names of each `permission.hook("evaluate")` event (never a
 * value) and which of them holds a session id; whether `permission.list` answers with a list; and
 * the `time` fields of a session when its turn ends.
 */
import { appendFileSync } from "node:fs";

interface Event {
  type: string;
  data: { sessionID?: string };
}

interface ProbeContext {
  options?: { out?: unknown };
  permission: {
    hook(
      name: "evaluate",
      callback: (event: object) => unknown,
    ): Promise<unknown>;
    list(input: { sessionID: string }): Promise<unknown>;
  };
  session: {
    get(input: {
      sessionID: string;
    }): Promise<{ time?: Record<string, number> }>;
  };
  event: {
    subscribe(options: {
      types: string[];
      signal: AbortSignal;
    }): AsyncIterable<Event>;
  };
}

/** Own and inherited property names of `value`, without `Object.prototype`'s. */
function namesOf(value: object): string[] {
  const names = new Set<string>();
  let level: object | null = value;
  while (level !== null && level !== Object.prototype) {
    for (const name of Object.getOwnPropertyNames(level)) {
      names.add(name);
    }
    level = Object.getPrototypeOf(level) as object | null;
  }
  return [...names].sort();
}

/** The line for one evaluate event: its field names, effect, action and the id-holding fields. */
function describe(event: object): Record<string, unknown> {
  const fields = event as Record<string, unknown>;
  const names = namesOf(event);
  return {
    evaluate: names,
    effect: fields["effect"],
    action: fields["action"],
    sessionFields: names.filter((name) => {
      const value = fields[name];
      return typeof value === "string" && value.startsWith("ses_");
    }),
  };
}

export default {
  id: "evaluate-probe",
  setup: (ctx: ProbeContext): (() => void) => {
    const out = ctx.options?.out;
    if (typeof out !== "string") {
      throw new Error("evaluate probe: the out option is missing");
    }
    const record = (line: Record<string, unknown>): void => {
      appendFileSync(out, `${JSON.stringify(line)}\n`);
    };
    const stop = new AbortController();
    ctx.permission
      .hook("evaluate", async (event) => {
        record(describe(event));
        const sessionID = (event as { sessionID?: string }).sessionID ?? "";
        const asks = await ctx.permission.list({ sessionID });
        record({ askList: Array.isArray(asks) });
      })
      .catch((error: unknown) => {
        record({ error: String(error) });
      });
    void (async () => {
      const types = ["session.execution.succeeded"];
      const events = ctx.event.subscribe({ types, signal: stop.signal });
      for await (const item of events) {
        const got = await ctx.session.get({
          sessionID: item.data.sessionID ?? "",
        });
        const time = got.time ?? {};
        record({
          timeKeys: Object.keys(time).sort(),
          idleAfterBusy: (time["idle"] ?? 0) >= (time["busy"] ?? Infinity),
        });
      }
    })();
    return () => {
      stop.abort();
    };
  },
};
