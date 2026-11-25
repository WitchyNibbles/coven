/** The 2.3.2 events and errors the plugin tests feed the fake context. */
export function notFound(): Error {
  return Object.assign(new Error("session not found"), {
    _tag: "Session.NotFoundError",
  });
}

export function started(sessionID: string): unknown {
  return { type: "session.execution.started", data: { sessionID } };
}

export function ended(
  sessionID: string,
  outcome: "succeeded" | "failed" | "interrupted" = "succeeded",
): unknown {
  return { type: `session.execution.${outcome}`, data: { sessionID } };
}

export function report(
  parentID: string,
  childID: string,
  state: string,
): unknown {
  return {
    type: "session.inbox.enqueued",
    data: {
      sessionID: parentID,
      item: {
        type: "synthetic",
        payload: {
          text: `<subagent sessionID="${childID}" state="${state}" description="kid">\nbody\n</subagent>`,
          description: "kid",
          metadata: { source: "subagent", childID, state },
        },
      },
    },
  };
}
