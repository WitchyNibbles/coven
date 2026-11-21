/** One item of a `waits on GM:` line: `QUESTION coven/6 since 01:43`. */
export type Ask = {
  /** The message prefix, `QUESTION`, `PROD-STEP`, ... */
  kind: string
  /** `project/task`. */
  ref: string
  /** `HH:MM`, or `?` when unknown. */
  time: string
}

/** One step of a PM's task list. */
export type Step = {
  subject: string
  /** `completed` is done, `in_progress` is the current step, `pending` is next. */
  status: 'done' | 'now' | 'next'
}

export type Witch = {
  name: string
  /** The first id `id=` prints (the first 8 characters of its session id); empty when there is none. */
  id: string
  state: string
  age: string
  /** Last message out, `HH:MM KIND ref: text`, without its delivery flag. */
  lastOut: string
  /** True when the last message out carries a NOT DELIVERED flag. */
  isUndelivered: boolean
  /** Last message in, `HH:MM KIND ref: text`, without its sender. */
  lastIn: string
  /** What it waits on the GM for, in the order printed; empty when it waits on nothing. */
  asks: Ask[]
  /** Its steps from the task list, in task order; empty when it has none or they cannot be read. */
  plan: Step[]
  /** Epoch ms until which the card is marked new; 0 when it is not. */
  freshUntil: number
}

export type Circle = {
  witches: Witch[]
  /** `HH:MM:SS` of the last successful read; empty before the first one. */
  checkedAt: string
  /** First line of why the last read failed; empty when it worked. */
  error: string
}

declare module 'claude-code' {
  interface PluginState {
    aquelarre: {
      circle: Shaped<Circle>
      /** A read is in flight: a tick that finds it set does nothing. */
      isReading: boolean
      /** Epoch ms when the last look began, whether or not it ran the status command; 0 before the first. */
      readStartedAt: number
      /** The read this window last took: when the status command finished (epoch ms; 0 before the first), the jobs fingerprint taken before it ran (null when none could be) and whether it failed. */
      lastRead: { at: number; fingerprint: string | null; isFailed: boolean }
    }
  }
}
