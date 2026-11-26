/**
 * Values the plugin relies on that only a real 2.3.2 server shows. Each check prints what it saw
 * as `# record <key>: <value>` (names and shapes, never secrets) and fails only when the server
 * does not answer at all.
 */
import { mkdir, symlink } from "node:fs/promises";
import { join } from "node:path";

import { at, itemsOf, stringOf } from "./api.ts";
import { probeLines } from "./copies.ts";
import { gmCall, pmId, record } from "./run.ts";
import type { Run } from "./run.ts";
import { createSession, infoOf, sleep } from "./sessions.ts";

/** The names and tags `manage.ts` reads as a session's own not-found (copied, not imported). */
const SESSION_NOT_FOUND = new Set([
  "NotFoundError",
  "Session.NotFoundError",
  "SessionNotFoundError",
]);

/** Whether a `session.get` error with this shape counts as "gone" by the rule in `manage.ts` (copied, not imported). */
function looksMissing(shape: Record<string, unknown>): boolean {
  const status = shape["status"] ?? shape["statusCode"];
  if (typeof status === "number") {
    return status === 404;
  }
  const labels = [shape["name"], shape["tag"]];
  return (
    labels.some(
      (label) => typeof label === "string" && SESSION_NOT_FOUND.has(label),
    ) || /^session not found$/i.test(String(shape["message"]))
  );
}

/** A second `PATCH` of metadata keeps the first one's keys: said for the top level and for a nested object. */
export async function mergeRecord(run: Run): Promise<string | undefined> {
  const id = await createSession(run.world, { directory: run.world.work });
  const patch = (metadata: object) =>
    run.world.api.patch(`/api/session/${id}`, { metadata });
  const first = await patch({ a: 1, coven: { role: "x" } });
  const second = await patch({ b: 2, coven: { code: "y" } });
  const metadata = at(await infoOf(run.world, id), "metadata");
  record("metadata update status", `${first.status} then ${second.status}`);
  record("metadata after two updates", JSON.stringify(metadata));
  const top =
    at(metadata, "a") === 1 && at(metadata, "b") === 2
      ? "merges top-level keys"
      : "replaces the whole metadata";
  const nested =
    at(metadata, "coven", "role") === "x"
      ? "merges nested objects"
      : "replaces nested objects";
  record("metadata update semantics", `${top}; ${nested}`);
  await run.world.api.del(`/api/session/${id}`);
  return first.status === 204 && second.status === 204
    ? undefined
    : `update answered ${first.status} and ${second.status}, not 204`;
}

/** What a listing item carries: metadata, parentID on a child, the outcome key and its values. */
export async function listShapeRecord(run: Run): Promise<string | undefined> {
  const answer = await run.world.api.get("/api/session");
  const items =
    (at(answer.body, "data") as Record<string, unknown>[] | undefined) ?? [];
  if (answer.status !== 200 || items.length === 0) {
    return `the list answered ${answer.status} with ${items.length} items`;
  }
  const pm = items.find((item) => item["id"] === pmId(run, "alpha"));
  const child = items.find((item) => typeof item["parentID"] === "string");
  record(
    "list item keys (a PM root)",
    Object.keys(pm ?? {})
      .sort()
      .join(","),
  );
  record(
    "list item has metadata",
    String(pm !== undefined && "metadata" in pm),
  );
  record("list item of a child has parentID", String(child !== undefined));
  const outcomes = [
    ...new Set(items.map((item) => String(item["outcome"]))),
  ].sort();
  record(
    "outcome key on list items",
    `${items.some((item) => "outcome" in item)}; values seen: ${outcomes.join(", ")}`,
  );
  const kinds = items.filter((item) => "metadata" in item).length;
  record("list items with metadata", `${kinds} of ${items.length}`);
  return pm === undefined ? "the PM is not in the list" : undefined;
}

/** The shape of `session.get` failing on a deleted session, read through the probe's tool. */
export async function notFoundRecord(run: Run): Promise<string | undefined> {
  const id = await createSession(run.world, { directory: run.world.work });
  const gone = await run.world.api.del(`/api/session/${id}`);
  const result = await gmCall(run, "probe_get", { sessionID: id });
  const shape = JSON.parse(result.text || "{}") as Record<string, unknown>;
  const http = await run.world.api.get(`/api/session/${id}`);
  record("delete answered", String(gone.status));
  record(
    "get of a deleted session over HTTP",
    `${http.status} ${JSON.stringify(http.body).slice(0, 160)}`,
  );
  record(
    "session.get error shape (name, tag, status, message)",
    `${String(shape["name"])}, ${String(shape["tag"])}, ${String(shape["status"])}, ${String(shape["message"])}`,
  );
  record("session.get error keys", JSON.stringify(shape["keys"]));
  record("the plugin's rule calls it gone", String(looksMissing(shape)));
  if (shape["found"] !== false) {
    return `probe_get said ${result.text.slice(0, 160)}`;
  }
  return looksMissing(shape)
    ? undefined
    : "coven_pm_list's rule would not read this error as a gone session, so it would show 'status unavailable'";
}

/** Polls the session's waiting asks until one shows or the wait runs out; says what shape the answer had. */
async function waitingAsk(
  run: Run,
  id: string,
): Promise<{ ask?: Record<string, unknown>; shape: string }> {
  let shape = "no answer";
  for (let tries = 0; tries < 100; tries += 1) {
    await sleep(100);
    const body = (await run.world.api.get(`/api/session/${id}/permission`))
      .body;
    shape = Array.isArray(body) ? "array" : "object with data";
    const [ask] = itemsOf(body);
    if (ask !== undefined) {
      return { ask, shape };
    }
  }
  return { shape };
}

/** The fields of a waiting ask: which one holds the request id. */
export async function askIdRecord(run: Run): Promise<string | undefined> {
  const id = await createSession(run.world, { directory: run.world.work });
  const first = await run.world.api.post(`/api/session/${id}/prompt`, {
    text: `DO shell ${JSON.stringify({ command: "echo asked" })}`,
  });
  const { ask, shape } = await waitingAsk(run, id);
  record("prompt answered", String(first.status));
  record("ask list shape", shape);
  record(
    "ask fields",
    Object.keys(ask ?? {})
      .sort()
      .join(","),
  );
  record(
    "ask id field",
    `id=${typeof ask?.["id"]} requestID=${typeof ask?.["requestID"]}`,
  );
  const requestID = stringOf(ask?.["id"]) || stringOf(ask?.["requestID"]);
  const reply =
    requestID === ""
      ? undefined
      : await run.world.api.post(
          `/api/session/${id}/permission/${requestID}/reply`,
          { decision: "reject" },
        );
  await run.world.api.post(`/api/session/${id}/interrupt`);
  record("ask reply answered", String(reply?.status));
  return ask === undefined
    ? "no ask was waiting for a shell call in a session with no role"
    : undefined;
}

/** What `create.before` reports as `cwd` for a shell call with `workdir: ""`. */
export async function emptyWorkdirRecord(
  run: Run,
): Promise<string | undefined> {
  const marker = "echo EMPTY-WORKDIR";
  const result = await gmCall(run, "shell", { command: marker, workdir: "" });
  await sleep(500);
  const seen = (await probeLines(run)).filter(
    (line) => line.kind === "create" && line.command === marker,
  );
  record(
    "shell with workdir empty: result",
    `${result.status} ${result.text.slice(0, 80)}`,
  );
  record(
    "create.before cwd for workdir empty",
    JSON.stringify(
      seen.map((line) => [
        line.copy.split("/").slice(-2).join("/"),
        line.cwd === "" ? "(empty)" : line.cwd?.split("/").slice(-2).join("/"),
      ]),
    ),
  );
  return seen.length === 0
    ? "create.before did not report the call"
    : undefined;
}

/** Whether `location.directory` is normalized when a session is made with a trailing slash or through a symlink. */
export async function directoryRecord(run: Run): Promise<string | undefined> {
  const link = join(run.world.home, "repos", "alpha-link");
  await mkdir(join(run.world.home, "repos"), { recursive: true });
  await symlink(run.world.alpha, link).catch(() => undefined);
  const made = new Map<string, string>([
    ["trailing slash", `${run.world.alpha}/`],
    ["symlink", link],
    ["double slash", run.world.alpha.replace("/repos/", "//repos/")],
  ]);
  for (const [name, directory] of made) {
    const id = await createSession(run.world, { directory });
    const got = at(await infoOf(run.world, id), "location", "directory");
    const kept =
      got === directory
        ? "kept as given"
        : got === run.world.alpha
          ? "normalized to the real folder"
          : `changed to ${String(got).replace(run.world.home, "<home>")}`;
    record(`location.directory for ${name}`, kept);
    await run.world.api.del(`/api/session/${id}`);
  }
  return undefined;
}

const MAX_PAGES = 20;

/** What `cursor.next` was: a string, null or absent. */
function describeCursor(cursor: unknown): string {
  if (cursor === null) {
    return "null";
  }
  return cursor === undefined ? "absent" : typeof cursor;
}

/** Follows `cursor.next` from the first page to the end; says how many pages, how many sessions and the last cursor. */
async function walkPages(
  run: Run,
  firstBody: unknown,
): Promise<{ pages: number; total: number; last: unknown }> {
  let pages = 0;
  let total = 0;
  let body = firstBody;
  let last: unknown = at(body, "cursor", "next");
  while (typeof last === "string" && pages < MAX_PAGES) {
    pages += 1;
    total += itemsOf(body).length;
    body = (
      await run.world.api.get(`/api/session?cursor=${encodeURIComponent(last)}`)
    ).body;
    last = at(body, "cursor", "next");
  }
  return { pages, total, last };
}

/** How many sessions a listing returns with no limit, whether `limit` is read, and what the last page's cursor says. */
export async function pageRecord(run: Run): Promise<string | undefined> {
  const made: string[] = [];
  for (let n = 0; n < 130; n += 1) {
    made.push(await createSession(run.world, { directory: run.world.work }));
  }
  const first = await run.world.api.get("/api/session");
  const count = itemsOf(first.body).length;
  record("session list page size (no limit)", String(count));
  const limited = await run.world.api.get("/api/session?limit=3");
  record(
    "session list with limit=3",
    `${limited.status}, ${itemsOf(limited.body).length} items`,
  );
  const walked = await walkPages(run, first.body);
  record(
    "session list: pages followed to the end",
    `${walked.pages} pages, ${walked.total} sessions before the last empty page`,
  );
  record(
    "session list: cursor.next on the last page",
    describeCursor(walked.last),
  );
  for (const id of made) {
    await run.world.api.del(`/api/session/${id}`);
  }
  return count > 0 ? undefined : "the list answered no sessions";
}
