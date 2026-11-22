import { setImmediate as tick } from "node:timers/promises";

import { JoinRecord } from "../src/join/record.ts";
import type { ReportState } from "../src/join/report.ts";
import { awaitAgents } from "../src/join/wait.ts";
import type { AwaitResult, HistoryItem, Host } from "../src/join/wait.ts";
import type { AwaitInput } from "../src/join/input.ts";

export const CALLER = "ses_lead";
export const NOTE = "Reports are in the conversation as <subagent> messages.";
export const A = "ses_a";
export const B = "ses_b";
export const C = "ses_c";

interface Sleep {
  signal: AbortSignal;
  resolve: () => void;
}

export function reportText(id: string, state: ReportState): string {
  return `<subagent sessionID="${id}" state="${state}" description="x">\nbody\n</subagent>`;
}

export function setup(ids: string[] = [A, B, C]) {
  const clock = { now: 1_000_000 };
  const record = new JoinRecord(() => clock.now);
  const sleeps: Sleep[] = [];
  const history: HistoryItem[] = [];
  const sessions = new Map<string, { id: string; parentID?: string }>(
    ids.map((id) => [id, { id, parentID: CALLER }]),
  );
  const progress: string[] = [];
  const controller = new AbortController();
  const host: Host = {
    getSession: (id) => Promise.resolve(sessions.get(id)),
    history: () => Promise.resolve([...history]),
    lastAssistantText: () => Promise.resolve("last words"),
    now: () => clock.now,
    sleep: (_ms, signal) =>
      new Promise((resolve) => {
        sleeps.push({ signal, resolve });
      }),
  };
  const call = {
    callerID: CALLER,
    signal: controller.signal,
    progress: (text: string) => progress.push(text),
  };
  const report = (id: string, state: ReportState, description = id) =>
    record.observe({
      kind: "report",
      parentID: CALLER,
      childID: id,
      state,
      description,
    });
  return {
    record,
    history,
    sessions,
    progress,
    sleeps,
    controller,
    report,
    start: (id: string) => record.observe({ kind: "started", sessionID: id }),
    end: (id: string) => record.observe({ kind: "ended", sessionID: id }),
    advance: async (ms: number) => {
      clock.now += ms;
      for (const sleep of sleeps) {
        sleep.resolve();
      }
      await tick();
    },
    run: (input: Partial<AwaitInput> = {}) => {
      const box: { result?: AwaitResult } = {};
      const promise = awaitAgents(
        { ids, mode: "all", ...input },
        call,
        record,
        host,
      ).then((result) => {
        box.result = result;
        return result;
      });
      return { box, promise };
    },
  };
}
