import type { PluginContext } from "./join/context.ts";

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

/** The join's context plus what the coven plugin adds: its options, `http.request` and `tool.list`. */
export interface CovenContext extends PluginContext {
  readonly options?: Readonly<Record<string, unknown>>;
  session: PluginContext["session"] & {
    hook(
      name: "http.request",
      callback: (event: HttpRequestEvent) => Promise<void> | void,
      options?: { providerID?: string },
    ): Promise<unknown>;
  };
  tool: PluginContext["tool"] & {
    /** Currently registered tools, after every transform, keyed by effective name. */
    list(): Promise<readonly { id: string }[]>;
  };
}
