import assert from "node:assert/strict";
import { test } from "node:test";

import { OptionsError, parseOptions } from "../src/options.ts";

/** Every key setup always writes; the tests below change one thing at a time. */
const BASE = {
  covenRoot: "/c",
  keyEnv: "K",
  profileDir: "/tmp/coven-test/profile",
  serveDir: "/tmp/coven-test/profile/state/serve",
  dataDir: "/tmp/coven-test/profile/data",
  store: null,
  secretRules: [],
};

await test("a minimal options object gets the defaults", () => {
  assert.deepEqual(parseOptions({ ...BASE, keyEnv: "GATEWAY_KEY" }), {
    ...BASE,
    keyEnv: "GATEWAY_KEY",
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
    dataDir: "/p/data",
    store: "/s",
    secretRules: [{ action: "read", resource: "*.cfg", effect: "deny" }],
    toolChoiceAuto: ["litellm", "other"],
    guardTimeoutMs: 600000,
  };
  assert.deepEqual(parseOptions(full), full);
});

await test("parses keyEnv, profileDir, serveDir, dataDir and store", () => {
  const parsed = parseOptions({
    ...BASE,
    profileDir: "/p",
    serveDir: "/p/state/serve",
    dataDir: "/p/data",
  });
  assert.equal(parsed.keyEnv, "K");
  assert.equal(parsed.profileDir, "/p");
  assert.equal(parsed.serveDir, "/p/state/serve");
  assert.equal(parsed.dataDir, "/p/data");
  assert.equal(parsed.store, null);
  assert.equal(parseOptions({ ...BASE, store: "/s" }).store, "/s");
});

const without = (key: string): Record<string, unknown> =>
  Object.fromEntries(Object.entries(BASE).filter(([name]) => name !== key));

const REFUSED: [string, unknown][] = [
  ["undefined", undefined],
  ["an empty object", {}],
  ["null", null],
  ["an array", []],
  ["an empty covenRoot", { ...BASE, covenRoot: "" }],
  ["a relative covenRoot", { ...BASE, covenRoot: "rel/path" }],
  ["a numeric covenRoot", { ...BASE, covenRoot: 1 }],
  ["a string toolChoiceAuto", { ...BASE, toolChoiceAuto: "litellm" }],
  ["an empty provider id", { ...BASE, toolChoiceAuto: [""] }],
  ["a numeric provider id", { ...BASE, toolChoiceAuto: [1] }],
  ["a timeout below the floor", { ...BASE, guardTimeoutMs: 4999 }],
  ["a timeout above the cap", { ...BASE, guardTimeoutMs: 600001 }],
  ["a fractional timeout", { ...BASE, guardTimeoutMs: 5000.5 }],
  ["a string timeout", { ...BASE, guardTimeoutMs: "5000" }],
  ["an unknown key", { ...BASE, covenroot: "/c" }],
  ["a missing covenRoot", without("covenRoot")],
  ["a missing profileDir", without("profileDir")],
  ["a missing serveDir", without("serveDir")],
  ["a missing dataDir", without("dataDir")],
  ["a relative dataDir", { ...BASE, dataDir: "data" }],
  ["a numeric dataDir", { ...BASE, dataDir: 1 }],
  ["a missing store", without("store")],
  ["a missing secretRules", without("secretRules")],
  ["a relative profileDir", { ...BASE, profileDir: "p" }],
  ["an empty profileDir", { ...BASE, profileDir: "" }],
  ["a numeric serveDir", { ...BASE, serveDir: 1 }],
  ["a relative store", { ...BASE, store: "s" }],
  ["a numeric store", { ...BASE, store: 1 }],
  ["secretRules that is not an array", { ...BASE, secretRules: {} }],
  [
    "a secret rule with an unknown effect",
    {
      ...BASE,
      secretRules: [{ action: "read", resource: "x", effect: "maybe" }],
    },
  ],
  [
    "a secret rule with no resource",
    { ...BASE, secretRules: [{ action: "read", effect: "deny" }] },
  ],
  ["a missing keyEnv", without("keyEnv")],
  ["an empty keyEnv", { ...BASE, keyEnv: "" }],
  ["a numeric keyEnv", { ...BASE, keyEnv: 1 }],
  ["a keyEnv with an equals sign", { ...BASE, keyEnv: "A=B" }],
  ["a keyEnv with a space", { ...BASE, keyEnv: "A B" }],
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

for (const key of [
  "keyEnv",
  "covenRoot",
  "profileDir",
  "serveDir",
  "dataDir",
  "store",
  "secretRules",
]) {
  await test(`a missing ${key} tells the user to run setup`, () => {
    assert.throws(() => parseOptions(without(key)), {
      message: `coven plugin options: ${key} is missing; run coven opencode setup`,
    });
  });
}
