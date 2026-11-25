import type { PluginContext } from "./join/context.ts";

/** One permission rule as the generated config writes it (`_rule` in `generate.py`). */
export interface PermissionRule {
  action: string;
  resource: string;
  effect: "allow" | "deny" | "ask";
}

/** 2.3.2 `promise/session.ts` `http.request`, with `Model.Ref` from `schema/src/model.ts`. */
export interface HttpRequestEvent {
  readonly sessionID: string;
  readonly agent: string;
  readonly model: {
    readonly id: string;
    readonly providerID: string;
    readonly variant?: string;
  };
  readonly kind: "primary" | "compaction" | "title" | "generate";
  request: Request;
}

/** 2.3.2 `promise/tool.ts` `execute.before`; a throw from the callback fails that tool call. */
export interface ExecuteBeforeEvent {
  readonly tool: string;
  readonly sessionID: string;
  readonly agent: string;
  readonly messageID: string;
  /** The call id. */
  readonly id: string;
  readonly input: unknown;
}

/**
 * 2.3.2 `shell.hook("create.before")`: runs before each shell command starts and carries no
 * session id (the event also has `command`, `cwd`, `timeout` and `shell`; the plugin reads none
 * of them). Edits to `env` reach the command.
 */
export interface ShellCreateEvent {
  env: Record<string, string>;
}

/** What `tool.hook` hands back: `dispose` removes the hook. */
export interface Registration {
  dispose(): void;
}

/**
 * The join's context plus what the coven plugin adds: its options, the session directory,
 * `http.request`, `execute.before`, `shell.create.before` and `tool.list`.
 */
export interface CovenContext extends PluginContext {
  readonly options?: Readonly<Record<string, unknown>>;
  /** `Location.Info` of 2.3.2: the directory the session works in. */
  readonly location: { readonly directory: string };
  session: PluginContext["session"] & {
    hook(
      name: "http.request",
      callback: (event: HttpRequestEvent) => Promise<void> | void,
      options?: { providerID?: string },
    ): Promise<unknown>;
  };
  shell: {
    hook(
      name: "create.before",
      callback: (event: ShellCreateEvent) => Promise<void> | void,
    ): Promise<unknown>;
  };
  tool: PluginContext["tool"] & {
    hook(
      name: "execute.before",
      callback: (event: ExecuteBeforeEvent) => Promise<void> | void,
    ): Promise<Registration>;
    /** Currently registered tools, after every transform, keyed by effective name. */
    list(): Promise<readonly { id: string }[]>;
  };
}
