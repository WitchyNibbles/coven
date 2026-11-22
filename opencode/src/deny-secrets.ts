import { isPlainObject } from "./join/fields.ts";

/** What `hooks/deny_secrets.py` reads on stdin: the Claude Code `PreToolUse` shape. */
export interface Payload {
  tool_name: string;
  tool_input: Record<string, unknown>;
  cwd: string;
  hook_event_name: "PreToolUse";
}

type Input = Record<string, unknown>;

interface Mapping {
  /** The Claude Code tool name the guard knows. */
  name: string;
  /** Claude field name to OpenCode field name. */
  fields: Readonly<Record<string, string>>;
}

const MAPPINGS: ReadonlyMap<string, Mapping> = new Map([
  [
    "shell",
    // `workdir` rides along: the guard searches every string of the call, so a `.ssh` workdir counts.
    { name: "Bash", fields: { command: "command", workdir: "workdir" } },
  ],
  ["read", { name: "Read", fields: { file_path: "path" } }],
  [
    "write",
    { name: "Write", fields: { file_path: "path", content: "content" } },
  ],
  [
    "edit",
    {
      name: "Edit",
      fields: {
        file_path: "path",
        old_string: "oldString",
        new_string: "newString",
      },
    },
  ],
  [
    "grep",
    {
      name: "Grep",
      fields: { pattern: "pattern", path: "path", glob: "include" },
    },
  ],
  ["glob", { name: "Glob", fields: { pattern: "pattern", path: "path" } }],
]);

/**
 * The header labels of OpenCode's patch grammar. Its parser (`util/src/patch.ts`) trims a line
 * before it matches one, so this does too, and the labels leave out the space after the colon so
 * that a header it would reject is still checked.
 */
const PATCH_LABELS = [
  "*** Add File:",
  "*** Update File:",
  "*** Move to:",
  "*** Delete File:",
];

function blocked(reason: string): Error {
  return new Error(`coven deny-secrets: ${reason}; blocking`);
}

function payload(tool_name: string, tool_input: Input, cwd: string): Payload {
  return { tool_name, tool_input, cwd, hook_event_name: "PreToolUse" };
}

/** The mapped fields that are present; an absent one stays out, as the guard expects. */
function pick(input: Input, fields: Mapping["fields"]): Input {
  const picked: Input = {};
  for (const [claude, opencode] of Object.entries(fields)) {
    if (input[opencode] !== undefined) {
      picked[claude] = input[opencode];
    }
  }
  return picked;
}

/** The path a patch line names when it is a file header, as OpenCode reads it; else `undefined`. */
function headerPath(line: string): string | undefined {
  const header = line.trim();
  const label = PATCH_LABELS.find((known) => header.startsWith(known));
  return label === undefined ? undefined : header.slice(label.length).trim();
}

/** Every path the patch adds, updates, moves to or deletes, in order. */
function patchPaths(input: Input): string[] {
  const text = input["patchText"];
  const named = (typeof text === "string" ? text.split("\n") : [])
    .map(headerPath)
    .filter((path): path is string => path !== undefined && path !== "");
  if (named.length === 0) {
    throw blocked("no file in patch");
  }
  return named;
}

/**
 * The guard payloads for one OpenCode tool call, or `"unchecked"` for a tool the guard does not
 * cover. A checked tool whose input is not an object is refused: the guard cannot be sure what it
 * would run.
 */
export function payloads(
  tool: string,
  input: unknown,
  cwd: string,
): Payload[] | "unchecked" {
  const mapping = MAPPINGS.get(tool);
  if (mapping === undefined && tool !== "patch") {
    return "unchecked";
  }
  if (!isPlainObject(input)) {
    throw blocked(`unreadable ${tool} input`);
  }
  if (mapping === undefined) {
    return patchPaths(input).map((path) =>
      payload("Write", { file_path: path }, cwd),
    );
  }
  return [payload(mapping.name, pick(input, mapping.fields), cwd)];
}
