/**
 * A plugin the pm-api e2e adds to the scratch profile. It runs one of two flows on load (see
 * `standalone.ts` and `served.ts`), on the real server, with the coven plugin's own code (the
 * profile holds a copy of it, next to this folder), and writes one JSON line each to the file named
 * by the `out` option. With a `trigger` option it runs the served flow. It writes ids, counts,
 * types and tool messages only.
 */
import { appendFileSync } from "node:fs";

import { served } from "./served.ts";
import type { Session, Write } from "./shared.ts";
import { standalone } from "./standalone.ts";

interface Context {
  options?: { out?: unknown; trigger?: unknown };
  session: Session;
}

export default {
  id: "pm-api-probe",
  setup: (ctx: Context): void => {
    const out = ctx.options?.out;
    const trigger = ctx.options?.trigger;
    if (typeof out !== "string") {
      throw new Error("pm-api probe: the out option is missing");
    }
    const record: Write = (line) => {
      appendFileSync(out, `${JSON.stringify(line)}\n`);
    };
    // not awaited: a plugin's setup returns before the server answers
    const flow =
      typeof trigger === "string"
        ? served(ctx.session, record, trigger)
        : standalone(ctx.session, record);
    flow.then(
      () => {
        record({ done: true });
      },
      (error: unknown) => {
        record({ probeError: String(error).slice(0, 300) });
      },
    );
  },
};
