/**
 * The parts of the OpenCode 2.x plugin context this plugin uses, typed locally so the
 * package needs no `@opencode/*` dependency.
 */

export interface SessionInfo {
  id: string;
  /** Missing or `null` for a root session. */
  parentID?: string | null;
  title?: string;
}

/** One message of `session.context`: user, synthetic and system carry `text`, assistant `content`. */
export interface ContextMessage {
  type: string;
  text?: string;
  metadata?: Record<string, unknown>;
  content?: readonly { type: string; text?: string }[];
}

export interface ContextHookEvent {
  readonly sessionID: string;
  system: { type: "text"; text: string }[];
}

export interface ToolCallContext {
  readonly sessionID: string;
  readonly signal: AbortSignal;
  progress(update: { status: string }): Promise<void>;
}

export interface ToolDefinition {
  name: string;
  description: string;
  input: Readonly<Record<string, unknown>>;
  options: { codemode: false };
  execute(
    input: unknown,
    context: ToolCallContext,
  ): Promise<{ content: string }>;
}

export interface PluginContext {
  event: {
    subscribe(options: {
      types: readonly string[];
      signal: AbortSignal;
    }): AsyncIterable<unknown>;
  };
  session: {
    get(input: { sessionID: string }): Promise<SessionInfo>;
    /**
     * Resolves once the session's agent loop is idle; at once when it already is. The 2.3.2 plugin
     * adapter drops `options`, so the signal does not cancel it there; callers stop listening.
     */
    wait(
      input: { sessionID: string },
      options?: { signal?: AbortSignal },
    ): Promise<void>;
    context(input: { sessionID: string }): Promise<readonly ContextMessage[]>;
    hook(
      name: "context",
      callback: (event: ContextHookEvent) => void,
    ): Promise<unknown>;
  };
  tool: {
    transform(
      callback: (editor: { add(tool: ToolDefinition): void }) => void,
    ): Promise<unknown>;
  };
}
