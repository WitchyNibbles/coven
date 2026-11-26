/**
 * A throwaway plugin of the isolation probe: it changes a shell call in the ways the PM isolation
 * would need and writes one JSON line per hook call to the `out` option's file. A call is changed
 * only when its command holds one of the case tags below, so every other shell runs as given.
 *
 * - `SHELLONLY`: `create.before` sets `event.shell` to the wrapper script in the `dir` option.
 * - `CMDONLY`: `create.before` replaces `event.command` and leaves `event.shell` alone.
 * - `SHELLFAIL`: the wrapper again; it exits 3 without running the command.
 * - `SHELLMISSING`: `event.shell` is a path that does not exist.
 * - `BOTH`: `create.before` sets the wrapper and replaces the command.
 * - `EXECARGS`: `execute.before` replaces `input.command` in place.
 * - `EXECREPLACE`: `execute.before` assigns a whole new `event.input` object.
 *
 * The lines hold names, strings the e2e itself wrote and flags: never an environment value.
 */
import { appendFileSync } from "node:fs";
import { join } from "node:path";

interface ShellEvent {
  command: string;
  cwd: string;
  shell?: unknown;
  timeout?: unknown;
  env: Record<string, string>;
}

interface ExecuteEvent {
  tool: string;
  sessionID: string;
  input: unknown;
}

interface ProbeContext {
  options?: { out?: unknown; dir?: unknown };
  location: { directory: string };
  tool: {
    hook(
      name: "execute.before",
      callback: (event: ExecuteEvent) => unknown,
    ): Promise<unknown>;
  };
  shell: {
    hook(
      name: "create.before",
      callback: (event: ShellEvent) => unknown,
    ): Promise<unknown>;
  };
}

/** Replacement command of a case: it writes `<dir>/<name>.marker` with the shell's view. */
function replacement(dir: string, name: string): string {
  const marker = join(dir, `${name}.marker`);
  return [
    `echo "CHANGED-${name}" > ${marker}`,
    `pwd >> ${marker}`,
    `echo "dollar0=$0" >> ${marker}`,
    `echo "exe=$(readlink /proc/$$/exe)" >> ${marker}`,
    `echo "CHANGED-${name}-stdout"`,
  ].join("; ");
}

/** The two `execute.before` changes: in place on the input object, or a whole new input. */
function mutateInput(event: ExecuteEvent, command: string, dir: string): void {
  const input = event.input as Record<string, unknown> | null;
  if (command.includes("EXECARGS") && input !== null) {
    input["command"] = replacement(dir, "execargs");
  }
  if (command.includes("EXECREPLACE")) {
    event.input = {
      ...input,
      command: replacement(dir, "execreplace"),
    };
  }
}

export default {
  id: "isolation-probe",
  setup: (ctx: ProbeContext): void => {
    const { out, dir } = ctx.options ?? {};
    if (typeof out !== "string" || typeof dir !== "string") {
      throw new Error("isolation probe: the out and dir options are missing");
    }
    const copy = ctx.location.directory;
    const record = (line: Record<string, unknown>): void => {
      appendFileSync(out, `${JSON.stringify({ copy, ...line })}\n`);
    };
    const wrapper = join(dir, "wrapper.sh");
    ctx.shell
      .hook("create.before", (event) => {
        const tagged = (tag: string): boolean => event.command.includes(tag);
        record({
          kind: "create",
          command: event.command.slice(0, 200),
          cwd: event.cwd,
          keys: Object.keys(event).sort(),
          shellType: typeof event.shell,
          shell: event.shell,
          timeout: event.timeout,
          // names only: which variables a coven or role setting added
          envNames: Object.keys(event.env).filter((name) =>
            /ROLE|COVEN/i.test(name),
          ),
        });
        if (tagged("SHELLONLY") || tagged("BOTH")) {
          event.shell = wrapper;
        }
        if (tagged("SHELLFAIL")) {
          event.shell = wrapper;
        }
        if (tagged("SHELLMISSING")) {
          event.shell = join(dir, "no-such-shell");
        }
        if (tagged("CMDONLY")) {
          event.command = replacement(dir, "cmdonly");
        }
        if (tagged("BOTH")) {
          event.command = replacement(dir, "both");
        }
        record({
          kind: "create-after",
          command: event.command.slice(0, 200),
          shell: event.shell,
        });
      })
      .catch((error: unknown) => {
        record({ kind: "error", error: String(error) });
      });
    ctx.tool
      .hook("execute.before", (event) => {
        const input = event.input as { command?: unknown } | null;
        const command = typeof input?.command === "string" ? input.command : "";
        if (event.tool !== "shell" && event.tool !== "bash") {
          return;
        }
        record({
          kind: "execute",
          tool: event.tool,
          command: command.slice(0, 200),
          inputKeys: Object.keys(input ?? {}).sort(),
          frozen: Object.isFrozen(event.input),
        });
        try {
          mutateInput(event, command, dir);
        } catch (error) {
          record({ kind: "error", error: `execute: ${String(error)}` });
        }
      })
      .catch((error: unknown) => {
        record({ kind: "error", error: String(error) });
      });
  },
};
