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
  /** Project codes a `coven_pm_launch` is running for, so two calls cannot both create one. */
  launching: Set<string>;
  /** Shell events a `createBefore` already took an entry for, so a second plugin copy takes none. */
  taken: WeakSet<object>;
  /** True while a `coven_gm_claim` is running, so two claims cannot both write `gm`. */
  claiming: boolean;
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
    launching: new Set(),
    taken: new WeakSet(),
    claiming: false,
  };
  return holder[KEY];
}
