/**
 * The served flow of the pm-api probe: a session archived for real, which only HTTP can do (the
 * harness holds the server password; the plugin never does). The probe makes a GM and two PMs,
 * says which PM to archive, waits for the harness's trigger file, then records what the real
 * session API and the coven plugin's own tools make of the archived PM: the type of
 * `time.archived` that `get` gives (names and types only), whether the default listing leaves it
 * out, and what `coven_pm_list` and `coven_pm_resume` say.
 */
import { existsSync } from "node:fs";

import { depsOf, lineOf, loadPlugin, runTool } from "./shared.ts";
import type { Session, Write } from "./shared.ts";

const WAIT_MS = 60_000;
const POLL_MS = 200;

async function triggered(trigger: string): Promise<boolean> {
  const deadline = Date.now() + WAIT_MS;
  while (!existsSync(trigger)) {
    if (Date.now() > deadline) {
      return false;
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  return true;
}

export async function served(
  session: Session,
  record: Write,
  trigger: string,
): Promise<void> {
  const plugin = await loadPlugin();
  const gm = await session.create({
    title: "gm",
    metadata: { coven: { role: "gm" } },
  });
  const archived = await session.create({
    title: "pm-y",
    metadata: { coven: { role: "pm", code: "y" } },
  });
  await session.create({
    title: "pm-z",
    metadata: { coven: { role: "pm", code: "z" } },
  });
  record({ ready: archived.id });
  if (!(await triggered(trigger))) {
    throw new Error("the harness never archived the session");
  }
  const got = await session.get({ sessionID: archived.id });
  const live = await session.list({ archived: false });
  const deps = depsOf(plugin, session);
  const line = async (input: object, code: string) =>
    lineOf(
      (await runTool(plugin.manage.pmListTool(deps), gm.id, input)).content,
      code,
    ) ?? null;
  record({
    getTimeKeys: Object.keys(got.time ?? {}).sort(),
    getArchivedType: typeof got.time?.["archived"],
    inLiveListing: live.data.some((each) => each.id === archived.id),
    listDefaultY: await line({}, "y"),
    listDefaultZ: await line({}, "z"),
    listArchivedY: await line({ archived: true }, "y"),
    resume: await runTool(plugin.manage.pmResumeTool(deps), gm.id, {
      code: "y",
      text: "back",
    }),
  });
}
