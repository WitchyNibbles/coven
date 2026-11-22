export type Mode = "all" | "any";

export interface AwaitInput {
  ids: string[];
  mode: Mode;
  timeoutSeconds?: number;
}

export class InputError extends Error {
  override name = "InputError";
}

const MAX_IDS = 16;
const MAX_TIMEOUT_SECONDS = 86400;
const KNOWN = new Set(["ids", "mode", "timeoutSeconds"]);

export const inputSchema = {
  type: "object",
  properties: {
    ids: {
      type: "array",
      items: { type: "string", minLength: 1 },
      minItems: 1,
      maxItems: MAX_IDS,
      uniqueItems: true,
    },
    mode: { type: "string", enum: ["all", "any"] },
    timeoutSeconds: {
      type: "integer",
      minimum: 1,
      maximum: MAX_TIMEOUT_SECONDS,
    },
  },
  required: ["ids"],
  additionalProperties: false,
} as const;

function show(value: unknown): string {
  return JSON.stringify(value) ?? String(value);
}

function parseIds(raw: unknown, callerID: string): string[] {
  if (!Array.isArray(raw)) {
    throw new InputError(
      `ids must be an array of session ids, got ${show(raw)}`,
    );
  }
  if (raw.length < 1 || raw.length > MAX_IDS) {
    throw new InputError(
      `ids must hold 1 to ${MAX_IDS} session ids, got ${raw.length}`,
    );
  }
  const seen = new Set<string>();
  for (const id of raw as unknown[]) {
    if (typeof id !== "string" || id === "") {
      throw new InputError(`ids must be non-empty strings, got ${show(id)}`);
    }
    if (id === callerID) {
      throw new InputError(`ids must not include the calling session ${id}`);
    }
    if (seen.has(id)) {
      throw new InputError(`ids must be unique, ${id} is repeated`);
    }
    seen.add(id);
  }
  return [...seen];
}

function parseMode(raw: unknown): Mode {
  if (raw === undefined) {
    return "all";
  }
  if (raw === "all" || raw === "any") {
    return raw;
  }
  throw new InputError(`mode must be "all" or "any", got ${show(raw)}`);
}

function parseTimeout(raw: unknown): number {
  if (
    typeof raw !== "number" ||
    !Number.isInteger(raw) ||
    raw < 1 ||
    raw > MAX_TIMEOUT_SECONDS
  ) {
    throw new InputError(
      `timeoutSeconds must be an integer from 1 to ${MAX_TIMEOUT_SECONDS}, got ${show(raw)}`,
    );
  }
  return raw;
}

export function parseInput(raw: unknown, callerID: string): AwaitInput {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new InputError(`input must be an object, got ${show(raw)}`);
  }
  const fields = raw as Record<string, unknown>;
  for (const name of Object.keys(fields)) {
    if (!KNOWN.has(name)) {
      throw new InputError(`unknown property ${name}`);
    }
  }
  const ids = parseIds(fields["ids"], callerID);
  const mode = parseMode(fields["mode"]);
  const timeout = fields["timeoutSeconds"];
  return timeout === undefined
    ? { ids, mode }
    : { ids, mode, timeoutSeconds: parseTimeout(timeout) };
}
