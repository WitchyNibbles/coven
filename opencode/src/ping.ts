import type { ToolDefinition } from "./join/context.ts";
import { VERSION } from "./version.ts";

/** The canary: a call to it proves the plugin loaded from this root. */
export function pingTool(covenRoot: string): ToolDefinition {
  return {
    name: "coven_ping",
    description:
      "Reports that the coven plugin is loaded: its name, version and root.",
    input: { type: "object", properties: {}, additionalProperties: false },
    options: { codemode: false },
    execute: () =>
      Promise.resolve({
        content: JSON.stringify({
          plugin: "coven",
          version: VERSION,
          covenRoot,
        }),
      }),
  };
}
