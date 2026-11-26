/**
 * The facts script: one `sh` file the suite writes (with the paths and the server's pid baked in)
 * and a session's shell runs. The server guard reads the command text only, so a shell that runs
 * `sh <file>` is the one way to look at the serve folder and the server's `/proc` entry from
 * inside a session; what the file does is what the namespace allows. It prints `KEY=[value]`
 * lines, which `factsOf` reads back.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { ROLE_ENV } from "../../../src/pm/role.ts";

export interface Places {
  serverPid: number;
  serve: string;
  data: string;
}

/** The facts one shell printed, by key. */
export type Facts = ReadonlyMap<string, string>;

/** A shell word for `value`: single-quoted. */
function word(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

/** The lines every session runs; `umount` is for a PM's shell only (a GM's runs on the real mounts). */
function body(places: Places, withUmount: boolean): string[] {
  const pid = places.serverPid;
  const lines = [
    `serve=${word(places.serve)}`,
    `data=${word(places.data)}`,
    `echo "SERVER_IN_PROC=[$([ -e /proc/${pid} ] && echo yes || echo no)]"`,
    `if cat /proc/${pid}/environ > /dev/null 2>&1; then echo "ENVIRON=[readable]"; else echo "ENVIRON=[failed]"; fi`,
    `echo "PROCESSES=[$(ls /proc | grep -c '^[0-9]')]"`,
    `echo "SERVE=[$(ls -A "$serve" 2>&1 | tr '\\n' ' ')]"`,
    `echo "DATA=[$(ls -A "$data" 2>&1 | tr '\\n' ' ')]"`,
    `echo "CWD=[$(pwd)]"`,
    `echo "ROLE=[$(printenv ${ROLE_ENV})]"`,
    `echo "SEAMS=[$(env | grep -c '^COVEN_ISOLATE_')]"`,
    `if git status --porcelain > /dev/null 2>&1; then echo "GIT=[ok]"; else echo "GIT=[failed]"; fi`,
    `if echo written > isolation-write.txt && [ "$(cat isolation-write.txt)" = written ]; then echo "WRITE=[ok]"; else echo "WRITE=[failed]"; fi`,
    `rm -f isolation-write.txt`,
  ];
  if (!withUmount) {
    return lines;
  }
  return [
    ...lines,
    `if umount /proc > /dev/null 2>&1; then echo "UMOUNT_PROC=[done]"; else echo "UMOUNT_PROC=[failed]"; fi`,
    `if umount "$serve" > /dev/null 2>&1; then echo "UMOUNT_SERVE=[done]"; else echo "UMOUNT_SERVE=[failed]"; fi`,
    `echo "SERVE_AFTER=[$(ls -A "$serve" 2>&1 | tr '\\n' ' ')]"`,
    `echo "SERVER_AFTER=[$([ -e /proc/${pid} ] && echo yes || echo no)]"`,
  ];
}

/** The text of the facts script. */
export function factsScript(places: Places, withUmount: boolean): string {
  return `#!/bin/sh\n${body(places, withUmount).join("\n")}\n`;
}

/** Writes the script to `<folder>/<name>` and returns its path. */
export async function writeFacts(
  folder: string,
  name: string,
  places: Places,
  withUmount: boolean,
): Promise<string> {
  await mkdir(folder, { recursive: true });
  const path = join(folder, name);
  await writeFile(path, factsScript(places, withUmount), { mode: 0o700 });
  return path;
}

/** The `KEY=[value]` lines of a shell's output. */
export function factsOf(text: string): Facts {
  const found = text.matchAll(/^([A-Z_]+)=\[(.*)\]$/gm);
  return new Map([...found].map((match) => [match[1] ?? "", match[2] ?? ""]));
}

/** One fact's value; the key's absence is a problem the check reports. */
export function fact(facts: Facts, key: string): string {
  return facts.get(key) ?? `<no ${key} line>`;
}
