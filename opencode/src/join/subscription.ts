import type { PluginContext } from "./context.ts";
import { EVENT_TYPES } from "./events.ts";
import { sleep } from "./host.ts";
import { noop } from "./wait-steps.ts";

const FIRST_DELAY_MS = 1000;
const LONGEST_DELAY_MS = 30_000;

/**
 * Feeds every event of the subscription to `handle` until `signal` aborts. A stream that
 * ends or fails is subscribed again after 1 s, doubling to 30 s; an event resets the delay.
 */
export async function follow(
  ctx: PluginContext,
  signal: AbortSignal,
  handle: (event: unknown) => void,
): Promise<void> {
  let delay = FIRST_DELAY_MS;
  const drain = async (): Promise<void> => {
    for await (const event of ctx.event.subscribe({
      types: EVENT_TYPES,
      signal,
    })) {
      delay = FIRST_DELAY_MS;
      handle(event);
    }
  };
  while (!signal.aborted) {
    await drain().catch(noop);
    await sleep(delay, signal);
    delay = Math.min(delay * 2, LONGEST_DELAY_MS);
  }
}
