import type { EndState, ReportState } from "./report.ts";

export type Observed =
  | { kind: "started"; sessionID: string }
  | { kind: "ended"; sessionID: string }
  | {
      kind: "report";
      parentID: string;
      childID: string;
      state: ReportState;
      description: string;
    }
  | { kind: "removed"; sessionID: string };

export interface ChildStatus {
  sessionID: string;
  description: string;
  end?: EndState;
  endedAt?: number;
}

const FORGET_AFTER_MS = 24 * 60 * 60 * 1000;

interface Child {
  parentID?: string;
  description: string;
  startSeq: number;
  reportSeq: number;
  reportCount: number;
  reportState?: ReportState;
  endedSeq: number;
  endedAt: number;
  removed: boolean;
  lastEventAt: number;
}

function newChild(now: number): Child {
  return {
    description: "",
    startSeq: 0,
    reportSeq: 0,
    reportCount: 0,
    endedSeq: 0,
    endedAt: 0,
    removed: false,
    lastEventAt: now,
  };
}

function finalReport(child: Child): EndState | undefined {
  const state = child.reportState;
  if (state === undefined || state === "running") {
    return undefined;
  }
  return child.reportSeq > child.startSeq ? state : undefined;
}

function endedWithoutReport(child: Child): boolean {
  return child.endedSeq > child.startSeq && child.reportSeq <= child.startSeq;
}

function describe(child: Child, sessionID: string): ChildStatus {
  const status: ChildStatus = { sessionID, description: child.description };
  if (child.removed) {
    status.end = "removed";
    return status;
  }
  const end = finalReport(child);
  if (end !== undefined) {
    status.end = end;
  } else if (endedWithoutReport(child)) {
    status.endedAt = child.endedAt;
  }
  return status;
}

/** What the plugin has seen about each parent's children, by observation order. */
export class JoinRecord {
  private readonly children = new Map<string, Child>();
  private readonly listeners = new Set<() => void>();
  private seq = 0;
  private readonly now: () => number;

  constructor(now: () => number) {
    this.now = now;
  }

  observe(event: Observed): void {
    this.apply(event);
    this.notify();
  }

  linkChild(parentID: string, childID: string, description: string): void {
    const child = this.entry(childID);
    child.parentID = parentID;
    child.description = description;
    this.notify();
  }

  status(parentID: string, childID: string): ChildStatus {
    const child = this.children.get(childID);
    if (child === undefined || !this.belongsTo(child, parentID)) {
      return { sessionID: childID, description: "" };
    }
    return describe(child, childID);
  }

  /** How many reports the record has seen for this child from this parent. */
  reportCount(parentID: string, childID: string): number {
    const child = this.children.get(childID);
    return child !== undefined && child.parentID === parentID
      ? child.reportCount
      : 0;
  }

  /** Linked children still at work: no end, and not ended without a report. */
  running(parentID: string): ChildStatus[] {
    const open: ChildStatus[] = [];
    for (const [childID, child] of this.children) {
      if (child.parentID === parentID) {
        const status = describe(child, childID);
        if (status.end === undefined && status.endedAt === undefined) {
          open.push(status);
        }
      }
    }
    return open;
  }

  /** Drops every parent whose last event is over 24 hours old. */
  forget(): void {
    const cutoff = this.now() - FORGET_AFTER_MS;
    const lastByParent = new Map<string, number>();
    for (const { parentID, lastEventAt } of this.children.values()) {
      if (parentID !== undefined) {
        lastByParent.set(
          parentID,
          Math.max(lastByParent.get(parentID) ?? 0, lastEventAt),
        );
      }
    }
    const stale = [...this.children]
      .filter(([, child]) => {
        const last =
          child.parentID === undefined
            ? child.lastEventAt
            : (lastByParent.get(child.parentID) ?? 0);
        return last < cutoff;
      })
      .map(([childID]) => childID);
    for (const childID of stale) {
      this.children.delete(childID);
    }
    if (stale.length > 0) {
      this.notify();
    }
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private belongsTo(child: Child, parentID: string): boolean {
    return child.parentID === undefined || child.parentID === parentID;
  }

  private entry(childID: string): Child {
    let child = this.children.get(childID);
    if (child === undefined) {
      child = newChild(this.now());
      this.children.set(childID, child);
    }
    child.lastEventAt = this.now();
    return child;
  }

  private apply(event: Observed): void {
    this.seq += 1;
    switch (event.kind) {
      case "started":
        this.entry(event.sessionID).startSeq = this.seq;
        return;
      case "ended": {
        const child = this.entry(event.sessionID);
        child.endedSeq = this.seq;
        child.endedAt = this.now();
        return;
      }
      case "removed":
        this.entry(event.sessionID).removed = true;
        return;
      case "report": {
        const child = this.entry(event.childID);
        child.parentID = event.parentID;
        child.description = event.description;
        child.reportSeq = this.seq;
        child.reportCount += 1;
        child.reportState = event.state;
        return;
      }
    }
  }

  private notify(): void {
    for (const listener of [...this.listeners]) {
      listener();
    }
  }
}
