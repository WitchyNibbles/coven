# coven.join for OpenCode

An OpenCode 2.x plugin that adds the tool `await_agents`: the lead agent passes the ids of its
subagents and the tool returns when they have sent their final report. The reports themselves stay
in the conversation as `<subagent>` messages; the tool returns states.

## How it waits

A call looks up every id, then ends when the children have sent their final report (`all`) or the
first one has (`any`), when `timeoutSeconds` runs out, or when the call is interrupted. It learns
of reports from three places:

- the OpenCode event stream, which the plugin follows from the start; it is the only place that
  sees a report arriving during the wait;
- the lead's conversation, read at the start of each call and every 15 s, which finds reports
  delivered before the call (for example before a plugin restart). OpenCode 2.3.2 adds a report to
  that conversation only after the running tool call returns, so this read cannot see one that
  arrives during the wait;
- `session.wait` for each child still pending: once the child is idle the plugin counts it as
  ended. A child that sent no report since its last start and sends no final one within 10 s comes
  back as `ended-without-report` with its last text, so the wait ends even when the event stream
  missed both the end and the report. A `running` report keeps the wait going.

## Tests

Unit tests run without OpenCode:

    npm test

## End-to-end run

The end-to-end run starts OpenCode 2.3.2 on a scripted fake model. Nothing leaves the machine: the
script runs inside `unshare -rn` (a network namespace with only `lo`), refuses to start anywhere
else, and refuses a project config that allows any provider but the fake one. It needs a 2.3.2
binary and `ip` (it brings `lo` up) and takes about two and a half minutes, because one check waits
a real 120 seconds.

    COVEN_OPENCODE_BIN=/path/to/opencode npm run e2e

Without `COVEN_OPENCODE_BIN` it prints `skipped: set COVEN_OPENCODE_BIN` and exits 0. Under
`node --test` it does nothing, because `main.ts` runs only when `COVEN_E2E=1`.

It prints one `ok` or `not ok` line for each check:

1. `all` and `any` return with 3 parallel workers.
2. Each worker report appears exactly once in the lead's history. This check passes without the
   plugin too, by design: it guards against the join adding a duplicate, not against a missing
   join.
3. A worker that sleeps 120 s is waited for.
4. A worker with its own worker: the wait ends after the final report.
5. An unknown id is named in the tool result.

The project under `test/e2e/project/` loads the plugin by re-exporting `src/plugin.ts`.
