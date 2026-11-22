import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import { KID, LEAD, boot, started } from "./plugin-fixture.ts";

await test("a subscription that ends is subscribed again after 1 s, then 2 s", async () => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  try {
    const p = await boot();
    p.streams[0]?.end();
    await tick();
    assert.equal(p.subscriptions.length, 1);
    mock.timers.tick(999);
    await tick();
    assert.equal(p.subscriptions.length, 1);
    mock.timers.tick(1);
    await tick();
    assert.equal(p.subscriptions.length, 2);
    p.streams[1]?.end();
    await tick();
    mock.timers.tick(1999);
    await tick();
    assert.equal(p.subscriptions.length, 2);
    mock.timers.tick(1);
    await tick();
    assert.equal(p.subscriptions.length, 3);
    p.cleanup();
  } finally {
    mock.timers.reset();
  }
});

await test("a subscribe that throws is retried, doubling to 30 s at most", async () => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  try {
    const p = await boot();
    const waits: number[] = [];
    let attempts = 0;
    p.ctx.event.subscribe = () => {
      attempts += 1;
      return {
        [Symbol.asyncIterator]: () => ({
          next: () => Promise.reject(new Error("refused")),
        }),
      };
    };
    p.streams[0]?.end();
    await tick();
    for (let round = 0; round < 8; round += 1) {
      const before = attempts;
      let waited = 0;
      while (attempts === before) {
        mock.timers.tick(500);
        waited += 500;
        await tick();
      }
      waits.push(waited);
    }
    assert.deepEqual(
      waits,
      [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000],
    );
    p.cleanup();
  } finally {
    mock.timers.reset();
  }
});

await test("an event after a resubscribe resets the delay to 1 s", async () => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  try {
    const p = await boot();
    p.streams[0]?.end();
    await tick();
    mock.timers.tick(1000);
    await tick();
    p.streams[1]?.end();
    await tick();
    mock.timers.tick(2000);
    await tick();
    assert.equal(p.subscriptions.length, 3);
    await p.emit(started(KID), 2);
    p.streams[2]?.end();
    await tick();
    mock.timers.tick(1000);
    await tick();
    assert.equal(p.subscriptions.length, 4);
    p.cleanup();
  } finally {
    mock.timers.reset();
  }
});

await test("the returned cleanup aborts the subscription and stops resubscribing", async () => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  try {
    const p = await boot();
    const signal = p.subscriptions[0]?.signal;
    assert.equal(signal?.aborted, false);
    p.cleanup();
    assert.equal(signal?.aborted, true);
    await tick();
    mock.timers.tick(60_000);
    await tick();
    assert.equal(p.subscriptions.length, 1);
  } finally {
    mock.timers.reset();
  }
});

await test("the record forgets stale parents once an hour", async () => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  try {
    const clock = { now: 1_000_000 };
    const p = await boot(clock);
    await p.emit(started(KID));
    assert.equal(p.reminder(LEAD).length, 1);
    clock.now += 25 * 60 * 60 * 1000;
    mock.timers.tick(60 * 60 * 1000);
    await tick();
    assert.deepEqual(p.reminder(LEAD), []);
    p.cleanup();
  } finally {
    mock.timers.reset();
  }
});
