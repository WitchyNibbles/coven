/** The value of `key` when it is a string, else `undefined`. */
export function ownString(
  record: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = record?.[key];
  return typeof value === "string" ? value : undefined;
}

/** A JSON object: not null and not an array. */
export function isPlainObject(
  value: unknown,
): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The value of `key` when it is a string with something in it. Anything else throws a `Fail`
 * (the caller's error type) that says what was given.
 */
export function nonEmptyString(
  raw: Record<string, unknown>,
  key: string,
  Fail: new (message: string) => Error,
): string {
  const value = raw[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Fail(`${key} must be a non-empty string, got ${show(value)}`);
  }
  return value;
}

/** `value` as JSON for an error message; `String(value)` when JSON has no form for it. */
export function show(value: unknown): string {
  return JSON.stringify(value) ?? String(value);
}

/** The message of an `Error`; `String(error)` for anything else that was thrown. */
export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
