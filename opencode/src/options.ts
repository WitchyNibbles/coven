import { isAbsolute } from "node:path";
import { isPlainObject, show } from "./join/fields.ts";

export interface CovenOptions {
  covenRoot: string;
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
const KNOWN = new Set(["covenRoot", "toolChoiceAuto", "guardTimeoutMs"]);

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
    toolChoiceAuto: parseProviders(fields["toolChoiceAuto"]),
    guardTimeoutMs: parseTimeout(fields["guardTimeoutMs"]),
  };
}
