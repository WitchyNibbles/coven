import type { Mode } from "./input.ts";
import type { EndState } from "./report.ts";

export interface HistoryItem {
  childID?: string;
  source?: string;
  text: string;
}

export interface Host {
  getSession(
    id: string,
  ): Promise<{ id: string; parentID?: string } | undefined>;
  history(sessionID: string): Promise<HistoryItem[]>;
  lastAssistantText(sessionID: string): Promise<string>;
  now(): number;
  sleep(ms: number, signal: AbortSignal): Promise<void>;
}

export interface CallContext {
  callerID: string;
  signal: AbortSignal;
  progress(status: string): void;
}

export interface DoneChild {
  sessionID: string;
  state: EndState;
  description: string;
  report?: string;
}

export interface AwaitResult {
  mode: Mode;
  waitedSeconds: number;
  done: DoneChild[];
  pending: string[];
  timedOut: boolean;
  note: string;
}

export class UnknownSessionError extends Error {}
export class NotAChildError extends Error {}
