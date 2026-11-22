import assert from "node:assert/strict";
import { test } from "node:test";

import { InputError, inputSchema, parseInput } from "../src/join/input.ts";

const CALLER = "ses_p";

function rejected(raw: unknown, mentions: string): void {
  assert.throws(
    () => parseInput(raw, CALLER),
    (error: unknown) =>
      error instanceof InputError && error.message.includes(mentions),
  );
}

await test("an input error carries the stable name InputError", () => {
  assert.throws(
    () => parseInput({ ids: [] }, CALLER),
    (error: unknown) =>
      error instanceof InputError && error.name === "InputError",
  );
});

await test("ids alone default to mode all", () => {
  assert.deepEqual(parseInput({ ids: ["ses_a"] }, CALLER), {
    ids: ["ses_a"],
    mode: "all",
  });
});

await test("mode any and timeoutSeconds pass through", () => {
  assert.deepEqual(
    parseInput(
      { ids: ["ses_a", "ses_b"], mode: "any", timeoutSeconds: 60 },
      CALLER,
    ),
    { ids: ["ses_a", "ses_b"], mode: "any", timeoutSeconds: 60 },
  );
});

await test("the limits of timeoutSeconds are accepted", () => {
  assert.equal(
    parseInput({ ids: ["ses_a"], timeoutSeconds: 1 }, CALLER).timeoutSeconds,
    1,
  );
  assert.equal(
    parseInput({ ids: ["ses_a"], timeoutSeconds: 86400 }, CALLER)
      .timeoutSeconds,
    86400,
  );
});

await test("sixteen ids are accepted", () => {
  const ids = Array.from({ length: 16 }, (_, index) => `ses_${index}`);
  assert.deepEqual(parseInput({ ids }, CALLER).ids, ids);
});

await test("input that is not an object is rejected", () => {
  rejected(undefined, "input");
  rejected(["ses_a"], "input");
});

await test("missing ids are rejected", () => {
  rejected({}, "ids");
});

await test("ids that are not an array are rejected", () => {
  rejected({ ids: "ses_a" }, "ses_a");
});

await test("an empty ids list is rejected", () => {
  rejected({ ids: [] }, "ids");
});

await test("more than sixteen ids are rejected", () => {
  const ids = Array.from({ length: 17 }, (_, index) => `ses_${index}`);
  rejected({ ids }, "17");
});

await test("a repeated id is rejected", () => {
  rejected({ ids: ["ses_a", "ses_a"] }, "ses_a");
});

await test("the caller's own id is rejected", () => {
  rejected({ ids: ["ses_p"] }, "ses_p");
});

await test("a non-string id is rejected", () => {
  rejected({ ids: [42] }, "42");
});

await test("an empty id is rejected", () => {
  rejected({ ids: [""] }, "ids");
});

await test("an unknown mode is rejected", () => {
  rejected({ ids: ["ses_a"], mode: "some" }, "some");
});

for (const bad of [0, 86401, 1.5, "60"]) {
  await test(`timeoutSeconds ${JSON.stringify(bad)} is rejected`, () => {
    rejected({ ids: ["ses_a"], timeoutSeconds: bad }, String(bad));
  });
}

await test("an unknown property is rejected", () => {
  rejected({ ids: ["ses_a"], wait: true }, "wait");
});

await test("the schema mirrors the checks", () => {
  assert.deepEqual(inputSchema, {
    type: "object",
    properties: {
      ids: {
        type: "array",
        items: { type: "string", minLength: 1 },
        minItems: 1,
        maxItems: 16,
        uniqueItems: true,
      },
      mode: { type: "string", enum: ["all", "any"] },
      timeoutSeconds: { type: "integer", minimum: 1, maximum: 86400 },
    },
    required: ["ids"],
    additionalProperties: false,
  });
});
