import { isAbsolute } from "node:path";
import type { PermissionRule } from "./context.ts";
import { isPlainObject, show } from "./join/fields.ts";

export interface CovenOptions {
  covenRoot: string;
  /** The name of the environment variable that holds the provider key; never its value. */
  keyEnv: string;
  /** The profile folder; empty when the config does not name it. */
  profileDir: string;
  /** The folder of the server's port and password files; empty when the config does not name it. */
  serveDir: string;
  /** The memory folder, or null when there is none. */
  store: string | null;
  /** The secret rules and the skills edit deny, as the generated config holds them. */
  secretRules: readonly PermissionRule[];
  toolChoiceAuto: readonly string[];
  guardTimeoutMs: number;
}

export class OptionsError extends Error {
  override name = "OptionsError";

  constructor(message: string) {
    super(`coven plugin options: ${message}`);
  }
}

const MIN_GUARD_TIMEOUT_MS = 5000;
const MAX_GUARD_TIMEOUT_MS = 600_000;
const KNOWN = new Set([
  "covenRoot",
  "keyEnv",
  "profileDir",
  "serveDir",
  "store",
  "secretRules",
  "toolChoiceAuto",
  "guardTimeoutMs",
]);
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

function parseRoot(raw: unknown): string {
  if (raw === undefined) {
    throw new OptionsError("covenRoot is missing; run coven opencode setup");
  }
  if (typeof raw !== "string" || !isAbsolute(raw)) {
    throw new OptionsError(
      `covenRoot must be a non-empty absolute path, got ${show(raw)}`,
    );
  }
  return raw;
}

function parseKeyEnv(raw: unknown): string {
  if (raw === undefined) {
    throw new OptionsError("keyEnv is missing; run coven opencode setup");
  }
  if (typeof raw !== "string" || !ENV_NAME.test(raw)) {
    throw new OptionsError(
      `keyEnv must be the name of an environment variable, got ${show(raw)}`,
    );
  }
  return raw;
}

function parseDir(raw: unknown, name: string): string {
  if (raw === undefined) {
    return "";
  }
  if (typeof raw !== "string" || !isAbsolute(raw)) {
    throw new OptionsError(
      `${name} must be a non-empty absolute path, got ${show(raw)}`,
    );
  }
  return raw;
}

function parseFolder(raw: unknown): string | null {
  if (raw === undefined || raw === null) {
    return null;
  }
  return parseDir(raw, "store");
}

const EFFECTS: ReadonlySet<unknown> = new Set(["allow", "deny", "ask"]);

function parseRule(raw: unknown): PermissionRule {
  if (
    !isPlainObject(raw) ||
    typeof raw["action"] !== "string" ||
    typeof raw["resource"] !== "string" ||
    !EFFECTS.has(raw["effect"])
  ) {
    throw new OptionsError(
      `secretRules must hold {action, resource, effect} rules, got ${show(raw)}`,
    );
  }
  return {
    action: raw["action"],
    resource: raw["resource"],
    effect: raw["effect"] as PermissionRule["effect"],
  };
}

function parseRules(raw: unknown): readonly PermissionRule[] {
  if (raw === undefined) {
    return [];
  }
  if (!Array.isArray(raw)) {
    throw new OptionsError(`secretRules must be an array, got ${show(raw)}`);
  }
  return (raw as unknown[]).map(parseRule);
}

function parseProviders(raw: unknown): readonly string[] {
  if (raw === undefined) {
    return [];
  }
  if (!Array.isArray(raw)) {
    throw new OptionsError(
      `toolChoiceAuto must be an array of provider ids, got ${show(raw)}`,
    );
  }
  for (const id of raw as unknown[]) {
    if (typeof id !== "string" || id === "") {
      throw new OptionsError(
        `toolChoiceAuto must hold non-empty strings, got ${show(id)}`,
      );
    }
  }
  return raw as string[];
}

function parseTimeout(raw: unknown): number {
  if (raw === undefined) {
    return MIN_GUARD_TIMEOUT_MS;
  }
  if (
    typeof raw !== "number" ||
    !Number.isInteger(raw) ||
    raw < MIN_GUARD_TIMEOUT_MS ||
    raw > MAX_GUARD_TIMEOUT_MS
  ) {
    throw new OptionsError(
      `guardTimeoutMs must be an integer from ${MIN_GUARD_TIMEOUT_MS} to ${MAX_GUARD_TIMEOUT_MS}, got ${show(raw)}`,
    );
  }
  return raw;
}

/** Checks the options object OpenCode hands the plugin and fills in the defaults. */
export function parseOptions(raw: unknown): CovenOptions {
  const given = raw ?? {};
  if (!isPlainObject(given)) {
    throw new OptionsError(`expected an object, got ${show(raw)}`);
  }
  const fields = given;
  for (const key of Object.keys(fields)) {
    if (!KNOWN.has(key)) {
      throw new OptionsError(`unknown option ${show(key)}`);
    }
  }
  return {
    covenRoot: parseRoot(fields["covenRoot"]),
    keyEnv: parseKeyEnv(fields["keyEnv"]),
    profileDir: parseDir(fields["profileDir"], "profileDir"),
    serveDir: parseDir(fields["serveDir"], "serveDir"),
    store: parseFolder(fields["store"]),
    secretRules: parseRules(fields["secretRules"]),
    toolChoiceAuto: parseProviders(fields["toolChoiceAuto"]),
    guardTimeoutMs: parseTimeout(fields["guardTimeoutMs"]),
  };
}
