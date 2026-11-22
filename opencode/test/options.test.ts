import assert from "node:assert/strict";
import { test } from "node:test";

import { OptionsError, parseOptions } from "../src/options.ts";

await test("a minimal options object gets the defaults", () => {
  assert.deepEqual(parseOptions({ covenRoot: "/c" }), {
    covenRoot: "/c",
    toolChoiceAuto: [],
    guardTimeoutMs: 5000,
  });
});

await test("a full valid options object passes through", () => {
  const full = {
    covenRoot: "/c",
    toolChoiceAuto: ["litellm", "other"],
    guardTimeoutMs: 600000,
  };
  assert.deepEqual(parseOptions(full), full);
});

const REFUSED: [string, unknown][] = [
  ["undefined", undefined],
  ["an empty object", {}],
  ["null", null],
  ["an array", []],
  ["an empty covenRoot", { covenRoot: "" }],
  ["a relative covenRoot", { covenRoot: "rel/path" }],
  ["a numeric covenRoot", { covenRoot: 1 }],
  ["a string toolChoiceAuto", { covenRoot: "/c", toolChoiceAuto: "litellm" }],
  ["an empty provider id", { covenRoot: "/c", toolChoiceAuto: [""] }],
  ["a numeric provider id", { covenRoot: "/c", toolChoiceAuto: [1] }],
  ["a timeout below the floor", { covenRoot: "/c", guardTimeoutMs: 4999 }],
  ["a timeout above the cap", { covenRoot: "/c", guardTimeoutMs: 600001 }],
  ["a fractional timeout", { covenRoot: "/c", guardTimeoutMs: 5000.5 }],
  ["a string timeout", { covenRoot: "/c", guardTimeoutMs: "5000" }],
  ["an unknown key", { covenRoot: "/c", covenroot: "/c" }],
];

for (const [name, raw] of REFUSED) {
  await test(`parseOptions refuses ${name}`, () => {
    assert.throws(
      () => parseOptions(raw),
      (error: unknown) =>
        error instanceof OptionsError &&
        error.name === "OptionsError" &&
        error.message.startsWith("coven plugin options:"),
    );
  });
}

await test("a missing covenRoot tells the user to run setup", () => {
  assert.throws(() => parseOptions({}), {
    message:
      "coven plugin options: covenRoot is missing; run coven opencode setup",
  });
});
