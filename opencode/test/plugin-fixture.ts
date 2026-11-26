import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { setImmediate as tick } from "node:timers/promises";

import { covenSetup } from "../src/coven.ts";
import type {
  CovenContext,
  EvaluateEvent,
  ExecuteBeforeEvent,
  SessionLike,
  SessionPromptInput,
  ShellCreateEvent,
} from "../src/context.ts";
import type {
  ContextHookEvent,
  ContextMessage,
  ToolCallContext,
  ToolDefinition,
} from "../src/join/context.ts";
import type { SpawnFn } from "../src/guard.ts";
import { joinSetup } from "../src/plugin.ts";
import { Stream } from "./plugin-stream.ts";
import { exits, fakeSpawn } from "./guard-fake.ts";
import { realPage } from "./pm-fake-session.ts";
import type { Behaviour } from "./guard-fake.ts";

/** This checkout: the `covenRoot` whose `hooks/pm-shell-isolate.sh` exists, as a PM's shell needs. */
export const COVEN_ROOT = resolve(import.meta.dirname, "../..");

/**
 * A profile folder with a serve folder in it, as the plugin finds one at start (it copies the
 * wrapper there); removed when the test process exits.
 */
const PROFILE_ROOT = mkdtempSync(join(tmpdir(), "coven-test-profile-"));
mkdirSync(join(PROFILE_ROOT, "state", "serve"), { recursive: true });
process.once("exit", () => {
  rmSync(PROFILE_ROOT, { recursive: true, force: true });
});

/** The options setup always writes besides `covenRoot` and `keyEnv`; the folders are real. */
export const PROFILE_OPTIONS = {
  profileDir: PROFILE_ROOT,
  serveDir: join(PROFILE_ROOT, "state", "serve"),
  dataDir: join(PROFILE_ROOT, "data"),
  store: null,
  secretRules: [],
};

/** The tools the hierarchy adds to the plugin. */
export const PM_TOOLS = [
  "coven_gm_claim",
  "coven_pm_launch",
  "coven_pm_list",
  "coven_pm_resume",
  "coven_pm_stop",
  "coven_send",
];

export const LEAD = "ses_lead";
export const KID = "ses_kid";
/** A PM session and a child of it (which has no role of its own). */
export const PM = "ses_pm";
export const PM_KID = "ses_pm_kid";
export const DIRECTORY = "/work/session";

/** Sessions per `list` page of the fake context. */
const PAGE_SIZE = 100;

export { ended, notFound, report, started } from "./plugin-events.ts";
import { notFound } from "./plugin-events.ts";

export interface Registered {
  name: string;
  options: unknown;
}

export interface ToolHook {
  name: string;
  callback: (event: ExecuteBeforeEvent) => Promise<void> | void;
  disposed: number;
}

export interface ShellHook {
  name: string;
  callback: (event: ShellCreateEvent) => Promise<void> | void;
}

interface Setup {
  clock?: { now: number };
  options?: Readonly<Record<string, unknown>>;
  /** Tool names the fake `tool.list` leaves out, as if they never registered. */
  unlisted?: readonly string[];
  /** False makes the shell and evaluate hooks return no registration to dispose. */
  disposable?: boolean;
  /** Makes the evaluate hook registration fail, after the shell one went through. */
  failEvaluate?: boolean;
  /** Makes disposing the execute.before hook throw. */
  guardDisposeThrows?: boolean;
  /** What each guard child the fake spawn starts does; it allows the call by default. */
  guard?: Behaviour;
  /** What the `python3` version check does; it passes by default, whatever `guard` does. */
  python?: Behaviour;
}

function makeWorld({
  clock = { now: 1_000_000 },
  options,
  unlisted = [],
  disposable = true,
  failEvaluate = false,
  guardDisposeThrows = false,
  guard = exits(0),
  python = exits(0),
}: Setup) {
  const streams: Stream[] = [];
  const subscriptions: { types: readonly string[]; signal: AbortSignal }[] = [];
  const here = { directory: DIRECTORY };
  const sessions = new Map<string, (SessionLike & { title?: string }) | Error>([
    [KID, { id: KID, parentID: LEAD, title: "kid" }],
    [LEAD, { id: LEAD, location: here, metadata: { coven: { role: "gm" } } }],
    [
      PM,
      {
        id: PM,
        location: here,
        metadata: { coven: { role: "pm", code: "proj" } },
      },
    ],
    [PM_KID, { id: PM_KID, parentID: PM }],
  ]);
  const disposals: string[] = [];
  const registration = (name: string): object =>
    disposable ? { dispose: () => disposals.push(name) } : {};
  const prompts: SessionPromptInput[] = [];
  const evaluateHooks: ((event: EvaluateEvent) => Promise<void> | void)[] = [];
  const asks = new Map<string, unknown[]>();
  const messages = new Map<string, ContextMessage[]>();
  const lookups: string[] = [];
  const hooks: ((event: ContextHookEvent) => void)[] = [];
  const tools: ToolDefinition[] = [];
  const registered: Registered[] = [];
  const toolHooks: ToolHook[] = [];
  const shellHooks: ShellHook[] = [];
  // The version check is a `python3` child of its own; `spawned` holds only the hook children.
  const fake = fakeSpawn(guard);
  const pythonFake = fakeSpawn(python);
  const spawn: SpawnFn = (command, args, env) =>
    (command === "python3" ? pythonFake : fake).spawn(command, args, env);
  const ctx: CovenContext = {
    ...(options === undefined ? {} : { options }),
    location: { directory: DIRECTORY },
    event: {
      subscribe: (subscription) => {
        subscriptions.push(subscription);
        const stream = new Stream();
        streams.push(stream);
        return stream.iterate(subscription.signal);
      },
    },
    session: {
      get: ({ sessionID }) => {
        lookups.push(sessionID);
        const found = sessions.get(sessionID);
        return found instanceof Error
          ? Promise.reject(found)
          : found === undefined
            ? Promise.reject(notFound())
            : Promise.resolve(found);
      },
      list: ({ parentID, cursor }) =>
        realPage(
          [...sessions.values()].filter(
            (found): found is SessionLike & { title?: string } =>
              !(found instanceof Error) &&
              (parentID === undefined || found.parentID === parentID),
          ),
          { cursor },
          PAGE_SIZE,
        ),
      create: () => Promise.reject(new Error("create is not faked")),
      prompt: (input) => {
        prompts.push(input);
        return Promise.resolve({});
      },
      interrupt: () => Promise.resolve({}),
      update: () => Promise.resolve(),
      wait: () => new Promise<void>(() => undefined),
      context: ({ sessionID }) =>
        Promise.resolve(messages.get(sessionID) ?? []),
      hook: ((
        name: string,
        callback: (event: ContextHookEvent) => void,
        hookOptions?: unknown,
      ) => {
        registered.push({ name, options: hookOptions });
        if (name === "context") {
          hooks.push(callback);
        }
        return Promise.resolve({});
      }) as CovenContext["session"]["hook"],
    },
    permission: {
      hook: (_name, callback) => {
        evaluateHooks.push(callback);
        return failEvaluate
          ? Promise.reject(new Error("evaluate refused"))
          : Promise.resolve(registration("evaluate"));
      },
      list: ({ sessionID }) => Promise.resolve(asks.get(sessionID) ?? []),
    },
    shell: {
      hook: (name, callback) => {
        shellHooks.push({ name, callback });
        return Promise.resolve(registration(name));
      },
    },
    tool: {
      transform: (callback) => {
        callback({ add: (tool) => tools.push(tool) });
        return Promise.resolve({});
      },
      hook: (name, callback) => {
        const entry: ToolHook = { name, callback, disposed: 0 };
        toolHooks.push(entry);
        return Promise.resolve({
          dispose: () => {
            entry.disposed += 1;
            if (guardDisposeThrows) {
              throw new Error("guard dispose failed");
            }
          },
        });
      },
      list: () =>
        Promise.resolve(
          tools
            .filter((tool) => !unlisted.includes(tool.name))
            .map((tool) => ({ id: tool.name })),
        ),
    },
  };
  return {
    ctx,
    clock,
    streams,
    subscriptions,
    sessions,
    messages,
    lookups,
    hooks,
    tools,
    registered,
    toolHooks,
    shellHooks,
    prompts,
    disposals,
    evaluateHooks,
    asks,
    spawn,
    spawned: fake.calls,
    pythonChecks: pythonFake.calls,
  };
}

function drive(world: ReturnType<typeof makeWorld>, cleanup: () => void) {
  const { streams, tools, hooks } = world;
  const reminder = (sessionID: string): string[] => {
    const event: ContextHookEvent = { sessionID, system: [] };
    hooks.forEach((hook) => hook(event));
    return event.system.map((part) => part.text);
  };
  const call = (
    input: unknown,
    sessionID = LEAD,
    signal = new AbortController().signal,
  ) => {
    const progress: string[] = [];
    const context: ToolCallContext = {
      sessionID,
      signal,
      progress: (update) => {
        progress.push(update.status);
        return Promise.resolve();
      },
    };
    const tool = tools[0];
    if (tool === undefined) {
      throw new Error("await_agents is not registered");
    }
    return { progress, result: tool.execute(input, context) };
  };
  return {
    ...world,
    cleanup,
    reminder,
    call,
    emit: async (event: unknown, stream = 0) => {
      streams[stream]?.push(event);
      await tick();
      await tick();
    },
  };
}

export async function boot(clock = { now: 1_000_000 }) {
  const world = makeWorld({ clock });
  const cleanup = await joinSetup(world.ctx, { now: () => clock.now });
  return drive(world, cleanup);
}

/** The whole coven plugin; setup may reject, so the world is returned beside the outcome. */
export function bootCoven(setup: Setup = {}) {
  const world = makeWorld(setup);
  const logs: string[] = [];
  const serveLogs: string[] = [];
  const outcome = covenSetup(
    world.ctx,
    {
      now: () => world.clock.now,
      log: (line) => logs.push(line),
      serveLog: (line) => serveLogs.push(line),
      spawn: world.spawn,
    },
    world.ctx.options,
  );
  const emit = async (event: unknown, stream = 0): Promise<void> => {
    world.streams[stream]?.push(event);
    await tick();
    await tick();
  };
  return { ...world, logs, serveLogs, outcome, emit };
}
