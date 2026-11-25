import { ShellQueue } from "./shell-env.ts";

/**
 * What the plugin's instances share. 2.3.2 evaluates the plugin module once per location, and
 * only `globalThis` is common to all of them (Task 0, step 7).
 */
export interface SharedState {
  queue: ShellQueue;
  /** PM sessions that sent a message to the GM this turn (set by coven_send, cleared at session.execution.started). */
  messaged: Set<string>;
  /** PM sessions the GM stopped, so no notice follows. */
  stopped: Set<string>;
  /** Keys of the tool calls running (`withInFlight`), so two calls of one kind cannot overlap. */
  inFlight: Set<string>;
  /** Shell events a `createBefore` already took an entry for, so a second plugin copy takes none. */
  taken: WeakSet<object>;
}

const KEY = Symbol.for("coven.opencode.shared");

type Holder = { [KEY]?: SharedState };

/** The one shared state of this process, made on first use. */
export function sharedState(): SharedState {
  const holder = globalThis as Holder;
  holder[KEY] ??= {
    queue: new ShellQueue(),
    messaged: new Set(),
    stopped: new Set(),
    inFlight: new Set(),
    taken: new WeakSet(),
  };
  return holder[KEY];
}

/**
 * Runs `body` while holding `key`. A call that finds the key held throws `refusal()` and holds
 * nothing. There is no `await` between the check and the hold, so two calls cannot both pass.
 */
export async function withInFlight<T>(
  key: string,
  refusal: () => Error,
  body: () => Promise<T>,
): Promise<T> {
  const { inFlight } = sharedState();
  if (inFlight.has(key)) {
    throw refusal();
  }
  inFlight.add(key);
  try {
    return await body();
  } finally {
    inFlight.delete(key);
  }
}
