import type { AwaitInput } from "./input.ts";
import type { ChildStatus, JoinRecord } from "./record.ts";
import {
  abortion,
  checkChild,
  lookupFailed,
  noop,
  reportsIn,
  snapshot,
} from "./wait-steps.ts";
import type {
  AwaitResult,
  CallContext,
  DoneChild,
  HistoryItem,
  Host,
  View,
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

const TIMED_OUT = Symbol("timed out");
type TimedOut = typeof TIMED_OUT;

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
  private readonly abortion: ReturnType<typeof abortion>;

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
    this.abortion = abortion(call.signal);
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
    try {
      return await this.execute();
    } finally {
      off();
      this.abortion.dispose();
    }
  }

  private async execute(): Promise<AwaitResult> {
    if (await this.checkChildren()) {
      return this.timedOutResult();
    }
    for (;;) {
      this.call.signal.throwIfAborted();
      if (this.host.now() >= this.nextHistory && (await this.readHistory())) {
        return this.timedOutResult();
      }
      this.changed = false;
      const view = await this.evaluate(this.host.now());
      if (view === TIMED_OUT) {
        return this.timedOutResult();
      }
      const now = this.host.now();
      if (this.isDone(view)) {
        return this.result(view, false);
      }
      if (this.deadline !== undefined && now >= this.deadline) {
        return this.result(view, true);
      }
      this.beat(now, view);
      await this.pause(now, view);
    }
  }

  /** Looks every id up before any waiting; true when the timeout came first. */
  private async checkChildren(): Promise<boolean> {
    const { callerID } = this.call;
    for (const id of this.input.ids) {
      const lookup = this.host.getSession(id).catch(lookupFailed(id));
      const session = await this.guarded(lookup);
      if (session === TIMED_OUT) {
        return true;
      }
      checkChild(id, callerID, session);
    }
    for (const id of this.input.ids) {
      const { description } = this.record.status(callerID, id);
      this.record.linkChild(callerID, id, description);
    }
    return false;
  }

  /**
   * Waits for a host call, but gives up when the call aborts (rethrowing its reason) or the
   * timeout runs out.
   */
  private async guarded<T>(op: Promise<T>): Promise<T | TimedOut> {
    op.catch(noop);
    const sleeper = new AbortController();
    try {
      const outcome = await Promise.race([
        op.then((value) => ({ value })),
        this.abortion.aborted,
        this.deadlineHit(sleeper.signal),
      ]);
      this.call.signal.throwIfAborted();
      return outcome === undefined ? TIMED_OUT : outcome.value;
    } finally {
      sleeper.abort();
    }
  }

  private deadlineHit(signal: AbortSignal): Promise<undefined> {
    if (this.deadline === undefined) {
      return new Promise<undefined>(noop);
    }
    const left = Math.max(0, this.deadline - this.host.now());
    return this.host.sleep(left, signal).then(
      () => undefined,
      () => undefined,
    );
  }

  private isDone(view: View): boolean {
    return this.input.mode === "all"
      ? view.pending.length === 0
      : view.done.length > 0;
  }

  /** The record as it stands when the timeout cut a host call short. */
  private timedOutResult(): AwaitResult {
    const view = snapshot(this.record, this.call.callerID, this.input.ids);
    return this.result(view, !this.isDone(view));
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
  /** True when the timeout came first. */
  private async readHistory(): Promise<boolean> {
    const { callerID } = this.call;
    const read = this.host.history(callerID);
    const fetched = await this.guarded(
      this.historyRead ? read.catch((): HistoryItem[] => []) : read,
    );
    if (fetched === TIMED_OUT) {
      return true;
    }
    const items = fetched;
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
    return false;
  }

  private async evaluate(now: number): Promise<View | TimedOut> {
    const view: View = { done: [], pending: [] };
    for (const id of this.input.ids) {
      const status = this.record.status(this.call.callerID, id);
      const done = await this.settle(status, now);
      if (done === TIMED_OUT) {
        return TIMED_OUT;
      }
      if (done === undefined) {
        view.pending.push(status);
      } else {
        view.done.push(done);
      }
    }
    return view;
  }

  private async settle(
    status: ChildStatus,
    now: number,
  ): Promise<DoneChild | TimedOut | undefined> {
    const { sessionID, description, end, endedAt } = status;
    if (end !== undefined) {
      return { sessionID, state: end, description };
    }
    if (endedAt === undefined || now - endedAt < NO_REPORT_GRACE_MS) {
      return undefined;
    }
    const report = await this.textAfter(sessionID, endedAt);
    return report === TIMED_OUT
      ? TIMED_OUT
      : { sessionID, state: "ended-without-report", description, report };
  }

  private async textAfter(
    sessionID: string,
    endedAt: number,
  ): Promise<string | TimedOut> {
    const known = this.lastText.get(sessionID);
    if (known?.endedAt === endedAt) {
      return known.text;
    }
    const text = await this.guarded(this.host.lastAssistantText(sessionID));
    if (text !== TIMED_OUT) {
      this.lastText.set(sessionID, { endedAt, text });
    }
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
  private async pause(now: number, view: View): Promise<void> {
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
      await Promise.race([woken, slept, this.abortion.aborted]);
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
  return await new Wait(input, call, record, host).run();
}
