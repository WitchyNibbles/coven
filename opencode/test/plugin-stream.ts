/** A hand-driven event stream: push feeds it, end closes it, iterate is the subscription. */
export class Stream {
  private readonly queue: unknown[] = [];
  private wake: (() => void) | undefined;
  private over = false;

  push(event: unknown): void {
    this.queue.push(event);
    this.wake?.();
  }

  end(): void {
    this.over = true;
    this.wake?.();
  }

  async *iterate(signal: AbortSignal): AsyncGenerator<unknown> {
    signal.addEventListener("abort", () => this.end());
    for (;;) {
      const next = this.queue.shift();
      if (next !== undefined) {
        yield next;
      } else if (this.over) {
        return;
      } else {
        await new Promise<void>((resolve) => {
          this.wake = resolve;
        });
      }
    }
  }
}
