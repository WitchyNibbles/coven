/** The value of `key` when it is a string, else `undefined`. */
export function ownString(
  record: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = record?.[key];
  return typeof value === "string" ? value : undefined;
}
