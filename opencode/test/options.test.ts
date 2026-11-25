import assert from "node:assert/strict";
import { test } from "node:test";

import { OptionsError, parseOptions } from "../src/options.ts";

await test("a minimal options object gets the defaults", () => {
  assert.deepEqual(parseOptions({ covenRoot: "/c", keyEnv: "GATEWAY_KEY" }), {
    covenRoot: "/c",
    keyEnv: "GATEWAY_KEY",
    profileDir: "",
    serveDir: "",
    store: null,
    secretRules: [],
    toolChoiceAuto: [],
    guardTimeoutMs: 5000,
  });
});

await test("a full valid options object passes through", () => {
  const full = {
    covenRoot: "/c",
    keyEnv: "GATEWAY_KEY",
    profileDir: "/p",
    serveDir: "/p/state/serve",
    store: "/s",
    secretRules: [{ action: "read", resource: "*.cfg", effect: "deny" }],
    toolChoiceAuto: ["litellm", "other"],
    guardTimeoutMs: 600000,
  };
  assert.deepEqual(parseOptions(full), full);
});

await test("parses keyEnv, profileDir, serveDir and store", () => {
  const parsed = parseOptions({
    covenRoot: "/c",
    keyEnv: "K",
    profileDir: "/p",
    serveDir: "/p/state/serve",
    store: null,
  });
  assert.equal(parsed.keyEnv, "K");
  assert.equal(parsed.profileDir, "/p");
  assert.equal(parsed.serveDir, "/p/state/serve");
  assert.equal(parsed.store, null);
  assert.throws(
    () => parseOptions({ covenRoot: "/c", profileDir: "/p" }),
    OptionsError,
  );
});

const REFUSED: [string, unknown][] = [
  ["undefined", undefined],
  ["an empty object", {}],
  ["null", null],
  ["an array", []],
  ["an empty covenRoot", { covenRoot: "" }],
  ["a relative covenRoot", { covenRoot: "rel/path" }],
  ["a numeric covenRoot", { covenRoot: 1 }],
  [
    "a string toolChoiceAuto",
    { covenRoot: "/c", keyEnv: "K", toolChoiceAuto: "litellm" },
  ],
  [
    "an empty provider id",
    { covenRoot: "/c", keyEnv: "K", toolChoiceAuto: [""] },
  ],
  [
    "a numeric provider id",
    { covenRoot: "/c", keyEnv: "K", toolChoiceAuto: [1] },
  ],
  [
    "a timeout below the floor",
    { covenRoot: "/c", keyEnv: "K", guardTimeoutMs: 4999 },
  ],
  [
    "a timeout above the cap",
    { covenRoot: "/c", keyEnv: "K", guardTimeoutMs: 600001 },
  ],
  [
    "a fractional timeout",
    { covenRoot: "/c", keyEnv: "K", guardTimeoutMs: 5000.5 },
  ],
  [
    "a string timeout",
    { covenRoot: "/c", keyEnv: "K", guardTimeoutMs: "5000" },
  ],
  ["an unknown key", { covenRoot: "/c", keyEnv: "K", covenroot: "/c" }],
  ["a relative profileDir", { covenRoot: "/c", keyEnv: "K", profileDir: "p" }],
  ["a numeric serveDir", { covenRoot: "/c", keyEnv: "K", serveDir: 1 }],
  ["a relative store", { covenRoot: "/c", keyEnv: "K", store: "s" }],
  [
    "secretRules that is not an array",
    { covenRoot: "/c", keyEnv: "K", secretRules: {} },
  ],
  [
    "a secret rule with an unknown effect",
    {
      covenRoot: "/c",
      keyEnv: "K",
      secretRules: [{ action: "read", resource: "x", effect: "maybe" }],
    },
  ],
  [
    "a secret rule with no resource",
    {
      covenRoot: "/c",
      keyEnv: "K",
      secretRules: [{ action: "read", effect: "deny" }],
    },
  ],
  ["a missing keyEnv", { covenRoot: "/c" }],
  ["an empty keyEnv", { covenRoot: "/c", keyEnv: "" }],
  ["a numeric keyEnv", { covenRoot: "/c", keyEnv: 1 }],
  ["a keyEnv with an equals sign", { covenRoot: "/c", keyEnv: "A=B" }],
  ["a keyEnv with a space", { covenRoot: "/c", keyEnv: "A B" }],
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

await test("a missing keyEnv tells the user to run setup", () => {
  assert.throws(() => parseOptions({ covenRoot: "/c" }), {
    message:
      "coven plugin options: keyEnv is missing; run coven opencode setup",
  });
});

await test("a missing covenRoot tells the user to run setup", () => {
  assert.throws(() => parseOptions({}), {
    message:
      "coven plugin options: covenRoot is missing; run coven opencode setup",
  });
});
