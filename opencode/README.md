# coven.join for OpenCode

An OpenCode 2.x plugin that adds the tool `await_agents`: the lead agent passes the ids of its
subagents and the tool returns when they have sent their final report. The reports themselves stay
in the conversation as `<subagent>` messages; the tool returns states.

## Tests

Unit tests run without OpenCode:

    npm test

## End-to-end run

The end-to-end run starts OpenCode 2.3.2 on a scripted fake model. Nothing leaves the machine: the
script runs inside `unshare -rn` (a network namespace with only `lo`), refuses to start anywhere
else, and refuses a project config that allows any provider but the fake one. It needs a 2.3.2
binary and `ip` (it brings `lo` up) and takes about three minutes, because one check waits a real
120 seconds.

    COVEN_OPENCODE_BIN=/path/to/opencode npm run e2e

Without `COVEN_OPENCODE_BIN` it prints `skipped: set COVEN_OPENCODE_BIN` and exits 0. Under
`node --test` it does nothing, because `main.ts` runs only when `COVEN_E2E=1`.

It prints one `ok` or `not ok` line for each check:

1. `all` and `any` return with 3 parallel workers.
2. Each worker report appears exactly once in the lead's history.
3. A worker that sleeps 120 s is waited for.
4. A worker with its own worker: the wait ends after the final report.
5. An unknown id is named in the tool result.

The project under `test/e2e/project/` loads the plugin by re-exporting `src/plugin.ts`.
