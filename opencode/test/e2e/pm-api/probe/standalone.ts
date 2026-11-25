/**
 * The standalone flow of the pm-api probe: a GM and a PM made on the real server, then the coven
 * plugin's own code run against the real `ctx.session`. One line per fact: what a full listing
 * returns (item A); what the plugin-side `update` does with `archived` (a record, not a check);
 * what `coven_pm_stop` with archive, `coven_pm_list` and `coven_pm_resume` do with the hidden
 * flag in the metadata; and what `coven_pm_list` shows for a PM deleted after the list was read.
 */
import { depsOf, lineOf, loadPlugin, runTool } from "./shared.ts";
import type { Plugin, Session, Write } from "./shared.ts";

/** The PM's metadata `coven` entry and its other keys, as the server holds them now. */
async function metadataOf(session: Session, sessionID: string) {
  const got = await session.get({ sessionID });
  return {
    flag: got.metadata?.coven?.["archived"],
    role: got.metadata?.coven?.["role"],
    code: got.metadata?.coven?.["code"],
    note: got.metadata?.["note"],
  };
}

async function listing(
  plugin: Plugin,
  session: Session,
  gmID: string,
  input: object,
): Promise<string | undefined> {
  const out = await runTool(
    plugin.manage.pmListTool(depsOf(plugin, session)),
    gmID,
    input,
  );
  return out.content ?? out.error;
}

/** Stop with archive, list, list with archived, resume: the hidden flag on the real server. */
async function flagFlow(
  plugin: Plugin,
  session: Session,
  ids: { gm: string; pm: string },
  record: Write,
): Promise<void> {
  const deps = depsOf(plugin, session);
  const stopped = await runTool(plugin.manage.pmStopTool(deps), ids.gm, {
    code: "x",
    archive: true,
  });
  const lineX = async (input: object) =>
    lineOf(await listing(plugin, session, ids.gm, input), "x") ?? null;
  record({
    stopArchive: stopped,
    afterStop: await metadataOf(session, ids.pm),
    listAfterStop: await lineX({}),
    listAllAfterStop: await lineX({ archived: true }),
  });
  const resumed = await runTool(plugin.manage.pmResumeTool(deps), ids.gm, {
    code: "x",
    text: "back",
  });
  record({
    resume: resumed,
    afterResume: await metadataOf(session, ids.pm),
    listAfterResume: await lineX({}),
  });
}

export async function standalone(
  session: Session,
  record: Write,
): Promise<void> {
  const plugin = await loadPlugin();
  const gm = await session.create({
    title: "gm",
    metadata: { coven: { role: "gm" } },
  });
  const pm = await session.create({
    title: "pm-x",
    metadata: { note: "kept", coven: { role: "pm", code: "x" } },
  });
  // item A: a full listing ends, on 2.3.2's null cursor after the last page
  try {
    const all = await plugin.busy.listAll(session, { archived: false });
    const ids = all.map((each) => each.id);
    record({
      listed: ids.length,
      holdsBoth: ids.includes(gm.id) && ids.includes(pm.id),
    });
  } catch (error) {
    record({ listedError: String(error).slice(0, 200) });
  }
  // a record, not a check: the plugin-side update has no archive (only HTTP PATCH archives)
  const updated = await session.update({ sessionID: pm.id, archived: true });
  const after = await session.get({ sessionID: pm.id });
  record({
    updateArchived: String(updated),
    archivedAfterUpdate: typeof after.time?.["archived"],
  });
  await flagFlow(plugin, session, { gm: gm.id, pm: pm.id }, record);
  // item C: the PM is listed, then its session goes; the list's get says it is gone
  const stale = await session.list({ archived: false });
  await session.remove({ sessionID: pm.id });
  const staleSession = {
    ...session,
    // the one page read before the delete, and the end of the listing
    list: () => Promise.resolve({ data: stale.data, cursor: { next: null } }),
  };
  record({ listAfterRemove: await listing(plugin, staleSession, gm.id, {}) });
}
