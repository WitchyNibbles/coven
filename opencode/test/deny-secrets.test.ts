import assert from "node:assert/strict";
import { test } from "node:test";

import { payloads } from "../src/deny-secrets.ts";

const CWD = "/work/project";

function one(tool: string, input: unknown): unknown {
  const result = payloads(tool, input, CWD);
  assert.notEqual(result, "unchecked");
  assert.equal(result.length, 1);
  return result[0];
}

function pre(tool_name: string, tool_input: Record<string, unknown>) {
  return { tool_name, tool_input, cwd: CWD, hook_event_name: "PreToolUse" };
}

await test("shell maps to Bash with the command", () => {
  assert.deepEqual(
    one("shell", { command: "cat x", workdir: "/elsewhere", timeout: 5 }),
    pre("Bash", { command: "cat x", workdir: "/elsewhere" }),
  );
});

await test("the shell workdir is checked text and never the payload cwd", () => {
  assert.deepEqual(
    one("shell", { command: "ls", workdir: "/elsewhere" }),
    pre("Bash", { command: "ls", workdir: "/elsewhere" }),
  );
  assert.deepEqual(
    one("shell", { command: "ls" }),
    pre("Bash", { command: "ls" }),
  );
});

await test("read maps to Read with file_path", () => {
  assert.deepEqual(
    one("read", { path: "a/b.txt", offset: 3, limit: 4 }),
    pre("Read", { file_path: "a/b.txt" }),
  );
});

await test("write maps to Write with file_path and content", () => {
  assert.deepEqual(
    one("write", { path: "a.txt", content: "hi" }),
    pre("Write", { file_path: "a.txt", content: "hi" }),
  );
});

await test("edit maps to Edit with snake case fields", () => {
  assert.deepEqual(
    one("edit", {
      path: "a.txt",
      oldString: "x",
      newString: "y",
      replaceAll: true,
    }),
    pre("Edit", { file_path: "a.txt", old_string: "x", new_string: "y" }),
  );
});

await test("grep maps include to glob and leaves absent fields out", () => {
  assert.deepEqual(
    one("grep", { pattern: "p", path: "src", include: "*.ts" }),
    pre("Grep", { pattern: "p", path: "src", glob: "*.ts" }),
  );
  assert.deepEqual(
    one("grep", { pattern: "p" }),
    pre("Grep", { pattern: "p" }),
  );
});

await test("glob maps to Glob with pattern and path", () => {
  assert.deepEqual(
    one("glob", { pattern: "**/*.md", path: "docs", limit: 5 }),
    pre("Glob", { pattern: "**/*.md", path: "docs" }),
  );
  assert.deepEqual(
    one("glob", { pattern: "*" }),
    pre("Glob", { pattern: "*" }),
  );
});

await test("every payload carries the given cwd", () => {
  const result = payloads("read", { path: "a" }, "/other/dir");
  assert.equal(result === "unchecked" ? "" : result[0]?.cwd, "/other/dir");
});

await test("patch gives one Write per file header", () => {
  const patchText = [
    "*** Begin Patch",
    "*** Add File: new.txt",
    "+hello",
    "*** Update File: a.txt",
    "@@",
    "-x",
    "+y",
    "*** Delete File: gone.txt",
    "*** End Patch",
  ].join("\n");
  assert.deepEqual(payloads("patch", { patchText }, CWD), [
    pre("Write", { file_path: "new.txt" }),
    pre("Write", { file_path: "a.txt" }),
    pre("Write", { file_path: "gone.txt" }),
  ]);
});

await test("patch checks the Move to target as well", () => {
  const patchText = [
    "*** Begin Patch",
    "*** Update File: a.txt",
    "*** Move to: .env",
    "@@",
    "-x",
    "+y",
    "*** End Patch",
  ].join("\n");
  assert.deepEqual(payloads("patch", { patchText }, CWD), [
    pre("Write", { file_path: "a.txt" }),
    pre("Write", { file_path: ".env" }),
  ]);
});

await test("patch headers with CRLF line ends and spaces are trimmed", () => {
  const patchText =
    "*** Begin Patch\r\n*** Add File:   spaced.txt  \r\n+x\r\n*** End Patch";
  assert.deepEqual(payloads("patch", { patchText }, CWD), [
    pre("Write", { file_path: "spaced.txt" }),
  ]);
});

await test("a patch with no file header is refused", () => {
  const patchText = "*** Begin Patch\n*** End Patch";
  assert.throws(
    () => payloads("patch", { patchText }, CWD),
    /^Error: coven deny-secrets: no file in patch; blocking$/,
  );
});

await test("a patch with an empty header path is refused", () => {
  assert.throws(
    () => payloads("patch", { patchText: "*** Add File:   \n+x" }, CWD),
    /no file in patch; blocking/,
  );
});

await test("a patch without text is refused", () => {
  assert.throws(
    () => payloads("patch", { patchText: 5 }, CWD),
    /no file in patch; blocking/,
  );
});

await test("any other tool is unchecked", () => {
  assert.equal(payloads("webfetch", { url: "https://x" }, CWD), "unchecked");
  assert.equal(payloads("coven_ping", {}, CWD), "unchecked");
  assert.equal(payloads("constructor", {}, CWD), "unchecked");
  assert.equal(payloads("toString", undefined, CWD), "unchecked");
});

await test("a non-object input for a checked tool fails closed", () => {
  for (const input of [undefined, null, "cat x", 3, ["a"]]) {
    for (const tool of ["shell", "read", "grep", "glob", "write", "edit"]) {
      assert.throws(
        () => payloads(tool, input, CWD),
        new RegExp(
          `^Error: coven deny-secrets: unreadable ${tool} input; blocking$`,
        ),
      );
    }
    assert.throws(
      () => payloads("patch", input, CWD),
      /^Error: coven deny-secrets: unreadable patch input; blocking$/,
    );
  }
});

function paths(patchText: string): string[] {
  const result = payloads("patch", { patchText }, CWD);
  assert.ok(Array.isArray(result));
  return result.map((entry) => String(entry.tool_input["file_path"]));
}

await test("an indented header is still a header", () => {
  const patch =
    "*** Begin Patch\n*** Add File: a.txt\n+x\n  *** Add File: .env\n+S=1\n*** End Patch";
  assert.deepEqual(paths(patch), ["a.txt", ".env"]);
});

await test("a tab, no-break space or BOM before a header is trimmed", () => {
  assert.deepEqual(paths("*** Begin Patch\n\t*** Delete File: .env"), [".env"]);
  assert.deepEqual(paths("*** Begin Patch\n *** Update File: .env"), [".env"]);
  assert.deepEqual(paths("*** Begin Patch\n﻿*** Add File: .env\n+x"), [".env"]);
});

await test("a carriage return inside a header path stays in the path", () => {
  const patch =
    "*** Begin Patch\n*** Add File: x\r/../.env\n+S=1\n*** End Patch";
  assert.deepEqual(paths(patch), ["x\r/../.env"]);
});

await test("line and paragraph separators do not cut a header path", () => {
  const patch = "*** Add File: x /../.env\n+S\n*** Move to: y .env";
  assert.deepEqual(paths(patch), ["x /../.env", "y .env"]);
});

await test("a header with no space after the colon is still checked", () => {
  assert.deepEqual(paths("*** Update File:.env"), [".env"]);
});

await test("a body line that only looks like a header is not a header", () => {
  assert.deepEqual(paths("*** Add File: a\n+*** Add File: b\n"), ["a"]);
});
