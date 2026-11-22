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

/** `value` as JSON for an error message; `String(value)` when JSON has no form for it. */
export function show(value: unknown): string {
  return JSON.stringify(value) ?? String(value);
}
