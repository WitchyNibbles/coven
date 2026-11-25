import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { GM_PREFIXES, PM_PREFIXES, headPrefix } from "../src/pm/prefixes.ts";

const TEXT_PY = new URL("../../src/coven/pm/text.py", import.meta.url);

/** The names in the Python tuple assigned to `name`. */
function pythonTuple(source: string, name: string): string[] {
  const found = new RegExp(`^${name} = \\(([^)]*)\\)`, "m").exec(source);
  assert.notEqual(found, null, `${name} not found in text.py`);
  const body = found?.[1] ?? "";
  return [...body.matchAll(/"([^"]+)"/g)].map((match) => match[1] ?? "");
}

await test("prefix lists match src/coven/pm/text.py", () => {
  const source = readFileSync(TEXT_PY, "utf8");
  assert.deepEqual(pythonTuple(source, "PM_PREFIXES"), [...PM_PREFIXES]);
  assert.deepEqual(pythonTuple(source, "GM_PREFIXES"), [...GM_PREFIXES]);
});

await test("headPrefix reads the first word of the first line", () => {
  assert.equal(headPrefix("ANSWER a/1: yes"), "ANSWER");
  assert.equal(headPrefix("  PROD-STEP\ta/1"), "PROD-STEP");
  assert.equal(headPrefix("DONE"), "DONE");
  assert.equal(headPrefix("DONE\nmore"), "DONE");
});

await test("headPrefix finds nothing without a known first word", () => {
  for (const text of [
    "",
    "   ",
    "hello",
    "done a/1",
    "DONE: a/1",
    "DONEX",
    "\nDONE a/1",
    "see DONE",
    "﻿DONE",
  ]) {
    assert.equal(headPrefix(text), undefined, JSON.stringify(text));
  }
});
