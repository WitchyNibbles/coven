import { execFile } from "node:child_process";
import { realpath } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { promisify } from "node:util";

import type { PermissionRule } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import {
  isPlainObject,
  messageOf,
  nonEmptyString,
  show,
} from "../join/fields.ts";
import { LINE_LIMIT, isValidCode } from "./code.ts";
import { findPm } from "./find.ts";
import { pmRules } from "./rules.ts";
import { withInFlight } from "./state.ts";
import { ToolError, asToolError, requireGm } from "./tools.ts";
import type { ToolDeps } from "./tools.ts";

const run = promisify(execFile);
const GIT_TIMEOUT_MS = 5000;

export interface LaunchDeps extends ToolDeps {
  options: {
    store: string | null;
    profileDir: string;
    secretRules: readonly PermissionRule[];
  };
}

interface LaunchInput {
  code: string;
  repo: string;
  brief: string;
  extraRepos: string[];
  title?: string;
}

function extraRepos(raw: unknown): string[] {
  if (raw === undefined) {
    return [];
  }
  if (!Array.isArray(raw)) {
    throw new ToolError(`extraRepos must be an array, got ${show(raw)}`);
  }
  return (raw as unknown[]).map((folder) => {
    if (typeof folder !== "string" || !isAbsolute(folder)) {
      throw new ToolError(
        `extraRepos must hold absolute paths, got ${show(folder)}`,
      );
    }
    const trimmed = folder.replace(/\/+$/, "");
    if (trimmed === "") {
      throw new ToolError("extraRepos must not hold the root folder");
    }
    return trimmed;
  });
}

function parseLaunch(raw: unknown): LaunchInput {
  if (!isPlainObject(raw)) {
    throw new ToolError(`input must be an object, got ${show(raw)}`);
  }
  const input: LaunchInput = {
    code: nonEmptyString(raw, "code", ToolError),
    repo: nonEmptyString(raw, "repo", ToolError),
    brief: nonEmptyString(raw, "brief", ToolError),
    extraRepos: extraRepos(raw["extraRepos"]),
  };
  if (raw["title"] !== undefined) {
    input.title = nonEmptyString(raw, "title", ToolError);
  }
  return input;
}

/**
 * The environment of the git run. git's own variables (`GIT_DIR` and the like) must not decide
 * which repository we ask about, and the C locale keeps its messages in the English the
 * "not a git repository" match reads.
 */
export function gitEnv(): NodeJS.ProcessEnv {
  const kept = Object.entries(process.env).filter(
    ([key]) => !key.startsWith("GIT_"),
  );
  return { ...Object.fromEntries(kept), LC_ALL: "C", LANG: "C" };
}

const NOT_A_CHECKOUT = /not a git repository|must be run in a work tree/;

/**
 * The top folder git reports for `dir`, or undefined when `dir` is not in a work tree. A git
 * that fails any other way (cannot start, timed out, broke) throws: that is not an answer.
 */
async function gitTop(dir: string): Promise<string | undefined> {
  try {
    const { stdout } = await run(
      "git",
      ["-C", dir, "rev-parse", "--show-toplevel"],
      { env: gitEnv(), timeout: GIT_TIMEOUT_MS },
    );
    return stdout.trim();
  } catch (error) {
    const stderr = isPlainObject(error) ? error["stderr"] : undefined;
    const line =
      (typeof stderr === "string" ? stderr : "").split("\n")[0] ?? "";
    if (NOT_A_CHECKOUT.test(line)) {
      return undefined;
    }
    throw new ToolError(`git failed: ${line.trim() || messageOf(error)}`);
  }
}

/**
 * The resolved top folder of the git checkout `dir` names (a worktree counts), or undefined when
 * `dir` is not that top folder.
 */
async function checkoutTop(dir: string): Promise<string | undefined> {
  if (!isAbsolute(dir)) {
    return undefined;
  }
  let real: string;
  try {
    real = await realpath(dir);
  } catch {
    return undefined;
  }
  const top = await gitTop(real);
  return top !== undefined && (await realpath(top)) === real ? real : undefined;
}

/** The store with its links resolved, or null when it has none or cannot be resolved. */
async function resolvedStore(store: string | null): Promise<string | null> {
  if (store === null) {
    return null;
  }
  try {
    return await realpath(store);
  } catch {
    return null;
  }
}

/** Refuses a launch that cannot go ahead; returns the checkout top folder the PM starts in. */
async function check(deps: LaunchDeps, input: LaunchInput): Promise<string> {
  if (!isValidCode(input.code)) {
    throw new ToolError(
      `${show(input.code)} is not a project code: lower-case letters, digits and dashes, ` +
        `starting with a letter or digit, at most ${LINE_LIMIT} characters`,
    );
  }
  if ((await findPm(deps.session, input.code)) !== undefined) {
    throw new ToolError(
      `code ${show(input.code)} already has a live PM; resume or stop it`,
    );
  }
  const top = await checkoutTop(input.repo);
  if (top === undefined) {
    throw new ToolError(
      `${show(input.repo)} is not a git checkout (an absolute path to its top folder)`,
    );
  }
  return top;
}

async function launch(
  deps: LaunchDeps,
  input: LaunchInput,
  top: string,
): Promise<string> {
  const { store, profileDir, secretRules } = deps.options;
  const created = await deps.session.create({
    agent: "project-manager",
    location: { directory: top },
    title: input.title ?? `pm-${input.code}`,
    metadata: { coven: { role: "pm", code: input.code } },
    permissions: pmRules({
      store,
      storeResolved: await resolvedStore(store),
      tmp: join(profileDir, "tmp"),
      secretRules,
      extraRepos: input.extraRepos,
    }),
  });
  try {
    await deps.session.prompt({ sessionID: created.id, text: input.brief });
  } catch (error) {
    throw new ToolError(
      `session ${created.id} for pm-${input.code} was created but its brief was not ` +
        `delivered: ${messageOf(error)}; resume it with coven_pm_resume`,
    );
  }
  return `launched pm-${input.code} as session ${created.id} in ${top}`;
}

export function pmLaunchTool(deps: LaunchDeps): ToolDefinition {
  return {
    name: "coven_pm_launch",
    description:
      "Launches a project manager: a session of the project-manager agent working in the " +
      "repo's checkout, with the PM permission rules, started with the brief. Refuses a code " +
      "that already has a live PM, a repo that is not a git checkout and an invalid code. " +
      "extraRepos are further absolute folders the PM may reach.",
    input: {
      type: "object",
      properties: {
        code: { type: "string", minLength: 1 },
        repo: { type: "string", minLength: 1 },
        brief: { type: "string", minLength: 1 },
        extraRepos: { type: "array", items: { type: "string" } },
        title: { type: "string", minLength: 1 },
      },
      required: ["code", "repo", "brief"],
      additionalProperties: false,
    },
    options: { codemode: false },
    execute: (raw, context) =>
      asToolError(async () => {
        // the GM check comes first: a refused caller never parses, never holds a lock
        await requireGm(deps, context.sessionID, "coven_pm_launch");
        const input = parseLaunch(raw);
        return withInFlight(
          `launch:${input.code}`,
          () => new ToolError(`already launching ${input.code}`),
          async () => {
            const top = await check(deps, input);
            return { content: await launch(deps, input, top) };
          },
        );
      }),
  };
}
