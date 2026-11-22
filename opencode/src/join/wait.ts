import type { AwaitInput } from "./input.ts";
import type { ChildStatus, JoinRecord } from "./record.ts";
import { abortion, checkChild, noop, reportsIn } from "./wait-steps.ts";
import type {
  AwaitResult,
  CallContext,
  DoneChild,
  HistoryItem,
  Host,
} from "./wait-types.ts";

export { NotAChildError, UnknownSessionError } from "./wait-types.ts";
export type {
  AwaitResult,
  CallContext,
  DoneChild,
  HistoryItem,
  Host,
} from "./wait-types.ts";

const HISTORY_EVERY_MS = 15_000;
const PROGRESS_EVERY_MS = 30_000;
const NO_REPORT_GRACE_MS = 10_000;
const NOTE = "Reports are in the conversation as <subagent> messages.";

interface View {
  done: DoneChild[];
  pending: ChildStatus[];
}

class Wait {
  private changed = false;
  private wake: (() => void) | undefined;
  private historyRead = false;
  private readonly start: number;
  private readonly deadline: number | undefined;
  private nextHistory: number;
  private nextProgress: number;
  private readonly lastText = new Map<
    string,
    { endedAt: number; text: string }
  >();

  private readonly input: AwaitInput;
  private readonly call: CallContext;
  private readonly record: JoinRecord;
  private readonly host: Host;

  constructor(
    input: AwaitInput,
    call: CallContext,
    record: JoinRecord,
    host: Host,
  ) {
    this.input = input;
    this.call = call;
    this.record = record;
    this.host = host;
    this.start = host.now();
    this.nextHistory = this.start;
    this.nextProgress = this.start + PROGRESS_EVERY_MS;
    this.deadline =
      input.timeoutSeconds === undefined
        ? undefined
        : this.start + input.timeoutSeconds * 1000;
  }

  async run(): Promise<AwaitResult> {
    const off = this.record.onChange(() => {
      this.changed = true;
      this.wake?.();
    });
    const { aborted, dispose } = abortion(this.call.signal);
    try {
      return await this.loop(aborted);
    } finally {
      off();
      dispose();
    }
  }

  private async loop(aborted: Promise<void>): Promise<AwaitResult> {
    for (;;) {
      this.call.signal.throwIfAborted();
      if (this.host.now() >= this.nextHistory) {
        await this.readHistory();
      }
      this.changed = false;
      const view = await this.evaluate(this.host.now());
      const now = this.host.now();
      if (this.isDone(view)) {
        return this.result(view, false);
      }
      if (this.deadline !== undefined && now >= this.deadline) {
        return this.result(view, true);
      }
      this.beat(now, view);
      await this.pause(aborted, now, view);
    }
  }

  private isDone(view: View): boolean {
    return this.input.mode === "all"
      ? view.pending.length === 0
      : view.done.length > 0;
  }

  private result(view: View, timedOut: boolean): AwaitResult {
    const elapsed = this.host.now() - this.start;
    return {
      mode: this.input.mode,
      waitedSeconds: Math.round(elapsed / 100) / 10,
      done: view.done,
      pending: view.pending.map((child) => child.sessionID),
      timedOut,
      note: NOTE,
    };
  }

  /** Feeds a report only while the record has seen fewer of them than the history holds. */
  private async readHistory(): Promise<void> {
    const { callerID } = this.call;
    let items: HistoryItem[];
    try {
      items = await this.host.history(callerID);
    } catch (error) {
      if (!this.historyRead) {
        throw error;
      }
      items = [];
    }
    this.historyRead = true;
    this.nextHistory = this.host.now() + HISTORY_EVERY_MS;
    const seen = new Map<string, number>();
    for (const { childID, state } of reportsIn(
      items,
      new Set(this.input.ids),
    )) {
      const nth = (seen.get(childID) ?? 0) + 1;
      seen.set(childID, nth);
      if (this.record.reportCount(callerID, childID) < nth) {
        this.record.observe({
          kind: "report",
          parentID: callerID,
          childID,
          state,
          description: this.record.status(callerID, childID).description,
        });
      }
    }
  }

  private async evaluate(now: number): Promise<View> {
    const view: View = { done: [], pending: [] };
    for (const id of this.input.ids) {
      const status = this.record.status(this.call.callerID, id);
      const { sessionID, description, end, endedAt } = status;
      if (end !== undefined) {
        view.done.push({ sessionID, state: end, description });
      } else if (endedAt !== undefined && now - endedAt >= NO_REPORT_GRACE_MS) {
        const report = await this.textAfter(sessionID, endedAt);
        view.done.push({
          sessionID,
          state: "ended-without-report",
          description,
          report,
        });
      } else {
        view.pending.push(status);
      }
    }
    return view;
  }

  private async textAfter(sessionID: string, endedAt: number): Promise<string> {
    const known = this.lastText.get(sessionID);
    if (known?.endedAt === endedAt) {
      return known.text;
    }
    const text = await this.host.lastAssistantText(sessionID);
    this.lastText.set(sessionID, { endedAt, text });
    return text;
  }

  private beat(now: number, view: View): void {
    if (now < this.nextProgress) {
      return;
    }
    this.nextProgress = now + PROGRESS_EVERY_MS;
    const names = view.pending.map(
      (child) => child.description || child.sessionID,
    );
    this.call.progress(
      `waiting for ${view.pending.length} of ${this.input.ids.length}: ${names.join(", ")}`,
    );
  }

  private nextWake(view: View): number {
    const times = [this.nextHistory, this.nextProgress];
    if (this.deadline !== undefined) {
      times.push(this.deadline);
    }
    for (const { endedAt } of view.pending) {
      if (endedAt !== undefined) {
        times.push(endedAt + NO_REPORT_GRACE_MS);
      }
    }
    return Math.min(...times);
  }

  /** Waits for a record change, the next deadline or the abort, whichever comes first. */
  private async pause(
    aborted: Promise<void>,
    now: number,
    view: View,
  ): Promise<void> {
    if (this.changed) {
      return;
    }
    const sleeper = new AbortController();
    const woken = new Promise<void>((resolve) => {
      this.wake = resolve;
    });
    const slept = this.host
      .sleep(Math.max(0, this.nextWake(view) - now), sleeper.signal)
      .catch(noop);
    try {
      await Promise.race([woken, slept, aborted]);
    } finally {
      this.wake = undefined;
      sleeper.abort();
    }
  }
}

/** One `await_agents` call: checks every id, then waits as `input.mode` says. */
export async function awaitAgents(
  input: AwaitInput,
  call: CallContext,
  record: JoinRecord,
  host: Host,
): Promise<AwaitResult> {
  call.signal.throwIfAborted();
  for (const id of input.ids) {
    await checkChild(id, call.callerID, host);
  }
  for (const id of input.ids) {
    record.linkChild(
      call.callerID,
      id,
      record.status(call.callerID, id).description,
    );
  }
  return new Wait(input, call, record, host).run();
}
