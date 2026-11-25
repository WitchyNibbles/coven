import { execFile } from "node:child_process";
import { realpath } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { promisify } from "node:util";

import type { PermissionRule } from "../context.ts";
import type { ToolDefinition } from "../join/context.ts";
import { isPlainObject, messageOf, show } from "../join/fields.ts";
import { LINE_LIMIT, isValidCode } from "./code.ts";
import { findPm } from "./find.ts";
import { pmRules } from "./rules.ts";
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

function text(raw: Record<string, unknown>, key: string): string {
  const value = raw[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new ToolError(
      `${key} must be a non-empty string, got ${show(value)}`,
    );
  }
  return value;
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
    return folder;
  });
}

function parseLaunch(raw: unknown): LaunchInput {
  if (!isPlainObject(raw)) {
    throw new ToolError(`input must be an object, got ${show(raw)}`);
  }
  const input: LaunchInput = {
    code: text(raw, "code"),
    repo: text(raw, "repo"),
    brief: text(raw, "brief"),
    extraRepos: extraRepos(raw["extraRepos"]),
  };
  if (raw["title"] !== undefined) {
    input.title = text(raw, "title");
  }
  return input;
}

/** git's own variables (`GIT_DIR` and the like) must not decide which repository we ask about. */
function cleanEnv(): NodeJS.ProcessEnv {
  return Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")),
  );
}

/** True when `dir` is the top folder of a git checkout (a worktree counts). */
async function isCheckout(dir: string): Promise<boolean> {
  if (!isAbsolute(dir)) {
    return false;
  }
  try {
    const { stdout } = await run(
      "git",
      ["-C", dir, "rev-parse", "--show-toplevel"],
      { env: cleanEnv(), timeout: GIT_TIMEOUT_MS },
    );
    return (await realpath(stdout.trim())) === (await realpath(dir));
  } catch {
    return false;
  }
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

async function check(deps: LaunchDeps, input: LaunchInput): Promise<void> {
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
  if (!(await isCheckout(input.repo))) {
    throw new ToolError(
      `${show(input.repo)} is not a git checkout (an absolute path to its top folder)`,
    );
  }
}

async function launch(deps: LaunchDeps, input: LaunchInput): Promise<string> {
  const { store, profileDir, secretRules } = deps.options;
  const created = await deps.session.create({
    agent: "project-manager",
    location: { directory: input.repo },
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
  return `launched pm-${input.code} as session ${created.id} in ${input.repo}`;
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
        await requireGm(deps, context.sessionID, "coven_pm_launch");
        const input = parseLaunch(raw);
        await check(deps, input);
        return { content: await launch(deps, input) };
      }),
  };
}
