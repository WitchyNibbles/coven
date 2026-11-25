import type { PluginContext } from "./join/context.ts";

/** One permission rule as the generated config writes it (`_rule` in `generate.py`). */
export interface PermissionRule {
  action: string;
  resource: string;
  effect: "allow" | "deny" | "ask";
}

/** One session as the plugin reads it from `ctx.session.get` and `list`. */
export interface SessionLike {
  id: string;
  parentID?: string;
  directory: string;
  /** `{coven: {role, code?}}` once coven has marked the session. */
  metadata?: Record<string, unknown>;
}

/** What `ctx.session.create` takes (checked live in Task 0, step 6). */
export interface SessionCreateInput {
  agent?: string;
  location?: { directory: string };
  parentID?: string;
  title?: string;
  metadata?: Record<string, unknown>;
  permissions?: readonly PermissionRule[];
}

/** What `ctx.session.update` changes (`PATCH /api/session/{id}`). */
export interface SessionUpdateInput {
  sessionID: string;
  metadata?: Record<string, unknown>;
  permissions?: readonly PermissionRule[];
  archived?: boolean;
}

/**
 * The `ctx.session` calls the plugin uses. `list` takes the query of `GET /api/session` and
 * returns a page; every location's sessions come back, with `metadata`.
 */
export interface SessionApi {
  create(input: SessionCreateInput): Promise<SessionLike>;
  get(input: { sessionID: string }): Promise<SessionLike>;
  list(input: {
    archived?: boolean;
    parentID?: string;
    directory?: string;
    search?: string;
    cursor?: string;
  }): Promise<{ items: readonly SessionLike[]; cursor?: string }>;
  prompt(input: {
    sessionID: string;
    text: string;
    delivery?: "queue" | "steer";
    resume?: boolean;
  }): Promise<unknown>;
  interrupt(input: { sessionID: string }): Promise<unknown>;
  update(input: SessionUpdateInput): Promise<unknown>;
}

/**
 * 2.3.2 `permission.hook("evaluate")`: the plugin may change `effect` and `reason` before the
 * session's tool call goes on.
 */
export interface EvaluateEvent {
  readonly sessionID: string;
  effect: PermissionRule["effect"];
  reason?: string;
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
 * session id (the event also has `timeout` and `shell`; the plugin reads neither). `command` is
 * the call's `command` and `cwd` its raw `workdir`, or the location directory when it had none
 * (Task 0, step 3). Edits to `env` reach the command; a throw stops it.
 */
export interface ShellCreateEvent {
  readonly command: string;
  readonly cwd: string;
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
