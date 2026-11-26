/**
 * The message prefixes between the GM and a PM. A contract test reads `src/coven/pm/text.py` and
 * fails when these lists and its `PM_PREFIXES` and `GM_PREFIXES` differ.
 */
export const PM_PREFIXES: readonly string[] = [
  "QUESTION",
  "PROD-STEP",
  "DRAFT",
  "DONE",
  "BLOCKED",
];
export const GM_PREFIXES: readonly string[] = [
  "ANSWER",
  "APPROVED",
  "DENIED",
  "TASK",
];

/** What Python's `str.split()` splits on, so a first word means the same on both sides. */
const SPACES = /[\p{White_Space}\x1c-\x1f]+/u;
const KNOWN: ReadonlySet<string> = new Set([...PM_PREFIXES, ...GM_PREFIXES]);

/**
 * The prefix a message starts with: the first word of its first line when that is a known prefix
 * (`parse_head` in `src/coven/pm/text.py`), else `undefined`.
 */
export function headPrefix(text: string): string | undefined {
  const line = text.split("\n", 1)[0] ?? "";
  const word = line.split(SPACES).find((part) => part !== "");
  return word !== undefined && KNOWN.has(word) ? word : undefined;
}
