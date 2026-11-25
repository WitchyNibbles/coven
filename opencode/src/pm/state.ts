import { ShellQueue } from "./shell-env.ts";

/**
 * What the plugin's instances share. 2.3.2 evaluates the plugin module once per location, and
 * only `globalThis` is common to all of them (Task 0, step 7).
 */
export interface SharedState {
  queue: ShellQueue;
  /** Sessions the GM has sent a message to. */
  messaged: Set<string>;
  /** PM sessions the GM stopped, so no notice follows. */
  stopped: Set<string>;
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
  };
  return holder[KEY];
}
