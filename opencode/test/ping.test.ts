import assert from "node:assert/strict";
import { test } from "node:test";

import { pingTool } from "../src/ping.ts";
import { VERSION } from "../src/version.ts";

await test("coven_ping answers with the plugin, version and root", async () => {
  const tool = pingTool("/c");
  assert.equal(tool.name, "coven_ping");
  assert.deepEqual(tool.input, {
    type: "object",
    properties: {},
    additionalProperties: false,
  });
  assert.deepEqual(tool.options, { codemode: false });
  const reply = await tool.execute(
    {},
    {
      sessionID: "ses_x",
      signal: new AbortController().signal,
      progress: () => Promise.resolve(),
    },
  );
  assert.deepEqual(JSON.parse(reply.content), {
    plugin: "coven",
    version: VERSION,
    covenRoot: "/c",
  });
});
