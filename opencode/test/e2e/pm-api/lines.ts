/** The lines the pm-api probe writes (`probe/standalone.ts`, `probe/served.ts`) and how to wait for them. */
import { readFile } from "node:fs/promises";

export interface ToolOut {
  content?: string;
  error?: string;
}

interface Metadata {
  flag?: unknown;
  role?: unknown;
  code?: unknown;
  note?: unknown;
}

export interface Recorded {
  listed?: number;
  holdsBoth?: boolean;
  listedError?: string;
  updateArchived?: string;
  archivedAfterUpdate?: string;
  stopArchive?: ToolOut;
  afterStop?: Metadata;
  /** The PM's list line, or null when the list left it out. */
  listAfterStop?: string | null;
  listAllAfterStop?: string | null;
  resume?: ToolOut;
  afterResume?: Metadata;
  listAfterResume?: string | null;
  listAfterRemove?: string;
  ready?: string;
  getTimeKeys?: string[];
  getArchivedType?: string;
  inLiveListing?: boolean;
  listDefaultY?: string | null;
  listDefaultZ?: string | null;
  listArchivedY?: string | null;
  probeError?: string;
  done?: boolean;
}

const POLL_MS = 250;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** The probe's lines written so far. */
export async function readLines(out: string): Promise<Recorded[]> {
  const text = await readFile(out, "utf8").catch(() => "");
  return text
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as Recorded);
}

/** The probe's lines once `until` holds for them, or a probe error, or after `waitMs`. */
export async function linesUntil(
  out: string,
  until: (lines: readonly Recorded[]) => boolean,
  waitMs: number,
): Promise<Recorded[]> {
  const deadline = Date.now() + waitMs;
  for (;;) {
    const lines = await readLines(out);
    const failed = lines.some((line) => line.probeError !== undefined);
    if (until(lines) || failed || Date.now() > deadline) {
      return lines;
    }
    await sleep(POLL_MS);
  }
}

export const isDone = (lines: readonly Recorded[]): boolean =>
  lines.some((line) => line.done === true);

/** The first value of `key` among the lines. */
export function found<K extends keyof Recorded>(
  lines: readonly Recorded[],
  key: K,
): Recorded[K] | undefined {
  return lines.find((line) => line[key] !== undefined)?.[key];
}
