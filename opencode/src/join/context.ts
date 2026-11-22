/**
 * The parts of the OpenCode 2.x plugin context this plugin uses, typed locally so the
 * package needs no `@opencode/*` dependency.
 */

export interface SessionInfo {
  id: string;
  parentID?: string;
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
