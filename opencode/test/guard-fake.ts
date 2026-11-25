import { EventEmitter } from "node:events";

import type { GuardChild, SpawnFn } from "../src/guard.ts";

/** One child the fake started: what it was asked to run, what it was fed, how it was stopped. */
export interface SpawnCall {
  command: string;
  args: readonly string[];
  stdin: string;
  kills: string[];
  /** The extra environment the child was started with; `undefined` when it inherits as is. */
  env: NodeJS.ProcessEnv | undefined;
}

export class FakeChild extends EventEmitter implements GuardChild {
  readonly stderr = Object.assign(new EventEmitter(), {
    setEncoding: () => undefined,
  });
  readonly stdin = Object.assign(new EventEmitter(), {
    end: (data: string) => {
      this.call.stdin += data;
    },
  });

  readonly call: SpawnCall;

  constructor(call: SpawnCall) {
    super();
    this.call = call;
  }

  kill(signal: "SIGKILL"): boolean {
    this.call.kills.push(signal);
    return true;
  }

  /** The child ends: its stderr first, then `close`, as Node orders them. */
  finish(code: number | null, stderr = "", signal: string | null = null): void {
    if (stderr !== "") {
      this.stderr.emit("data", stderr);
    }
    this.emit("close", code, signal);
  }
}

export type Behaviour = (child: FakeChild) => void;

export const exits =
  (code: number, stderr = ""): Behaviour =>
  (child) => {
    child.finish(code, stderr);
  };

export const never: Behaviour = () => undefined;

/** A spawn that starts no process: each child acts out `behaviour` once its listeners are attached. */
export function fakeSpawn(behaviour: Behaviour = exits(0)) {
  const calls: SpawnCall[] = [];
  const spawn: SpawnFn = (command, args, env) => {
    const call: SpawnCall = { command, args, stdin: "", kills: [], env };
    calls.push(call);
    const child = new FakeChild(call);
    setImmediate(() => behaviour(child));
    return child;
  };
  return { spawn, calls };
}
