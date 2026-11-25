import assert from "node:assert/strict";
import { test } from "node:test";

import type { SessionApi, SessionLike } from "../src/context.ts";
import { descendants, listAll } from "../src/pm/busy.ts";
import { findPm } from "../src/pm/find.ts";
import { coven, realPage } from "./pm-fake-session.ts";

/** A `ctx.session` over `all` that pages like 2.3.2 and records each query. */
function realPaging(all: readonly SessionLike[], size: number) {
  const queries: Record<string, unknown>[] = [];
  const session = {
    list(query: { parentID?: string; cursor?: string | null }) {
      queries.push(query);
      const wanted = all.filter(
        (each) =>
          query.parentID === undefined || each.parentID === query.parentID,
      );
      return realPage(wanted, query, size);
    },
  };
  return { session: session as unknown as SessionApi, queries };
}

await test("a listing ends on the null cursor of the page after the last, and never sends a null cursor", async () => {
  const all = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const { session, queries } = realPaging(all, 2);
  assert.deepEqual(
    (await listAll(session, { archived: false })).map((each) => each.id),
    ["a", "b", "c"],
  );
  assert.equal(queries.length, 3);
  assert.equal(
    queries.some((query) => query["cursor"] === null),
    false,
  );
});

await test("an empty listing ends on its first page", async () => {
  const { session, queries } = realPaging([], 2);
  assert.deepEqual(await listAll(session, {}), []);
  assert.equal(queries.length, 1);
});

await test("the tree walk and the PM lookup end on the null cursor too", async () => {
  const all = [
    { id: "pm1", metadata: coven("pm", "one") },
    { id: "kid", parentID: "pm1" },
    { id: "grandkid", parentID: "kid" },
  ];
  const { session } = realPaging(all, 1);
  assert.deepEqual(
    (await descendants(session, "pm1")).map((each) => each.id),
    ["kid", "grandkid"],
  );
  assert.equal((await findPm(session, "one"))?.id, "pm1");
  assert.equal(await findPm(session, "other"), undefined);
});
