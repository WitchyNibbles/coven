# OpenCode join tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An OpenCode 2.x plugin tool `await_agents` that blocks a lead until its subagents have
sent their final report, plus a reminder of running children before each model request.

**Architecture:** A pure core (`record.ts` keeps what the plugin has seen, `wait.ts` runs one
call) over a small `Host` interface, and a thin `plugin.ts` that adapts OpenCode's `ctx` to it.
The report text reaches the lead once, through 2.x's own `<subagent>` inbox message; the tool
returns states only.

**Tech Stack:** TypeScript, Node 24 (`node --test` with type stripping), c8, OpenCode 2.3.2
plugin API (no `@opencode/*` dependency: a local structural type of the parts we use).

**Spec:** `docs/specs/2026-11-22-opencode-join-design.md`

## Global Constraints

- Package lives at `opencode/` (user's choice 2026-11-22), a TypeScript project under the repo's
  code standard: `package.json`, `package-lock.json`, `tsconfig.json` extending
  `../.coven/tsconfig.base.json`, `eslint.config.mjs` spreading `../.coven/eslint.config.mjs`.
- Dev dependencies only: `eslint`, `prettier`, `typescript`, `typescript-eslint`, `c8`,
  `@types/node`, pinned to the versions in `tests/fixtures/ts-project/package.json`. No runtime
  dependencies.
- No suppressions of any kind (`eslint-disable`, `@ts-ignore`, `@ts-expect-error`,
  `c8 ignore`, `prettier-ignore`). Complexity ≤ 10, ≤ 300 lines a file.
- Nothing names a model or a provider. Nothing writes files or logs.
- Tests never reach the network. Anything that starts OpenCode runs inside `unshare -rn` with the
  scripted fake model only.
- Never name the private memory store or the work harness; the local pre-commit refuses one word.
- Commits: made with the repo's public author identity, passed with `git -c` on the commit itself
  (never by changing git config), no `Co-Authored-By`, no "Generated with" line.
- Limits from the spec: `ids` 1-16, unique, not the caller; `mode` `all` (default) or `any`;
  `timeoutSeconds` integer 1-86400, optional; progress every 30 s; history re-read every 15 s;
  no-report grace 10 s; resubscribe backoff 1 s doubling to 30 s; record forgets a parent 24 h
  after its last event.

## Review Focus

- A child whose round ends with a `running` report (it has its own subagent): the wait must not
  end and must not fire `ended-without-report` after 10 s. Test in Task 2.
- A report that arrived before the plugin saw the child start (the history path): the wait ends
  at once. Test in Task 3.
- An abort while several children are pending: the call ends with the abort, timers are cleared
  and no progress fires after. Test in Task 3.
- A second `any` over ids already done returns at once with them listed in `done`. Test in Task 3.
- The event stream ending mid-wait: the history re-read cannot see a report that arrives during
  the wait (2.3.2 delivers it to the lead's context only after the tool result), so the
  `session.wait` reconcile ends the wait through the no-report grace, and the plugin resubscribes.
  Tests in Tasks 3 and 4 and the final fix wave.

---

## File structure

- `opencode/package.json`, `package-lock.json`, `tsconfig.json`, `eslint.config.mjs`,
  `.gitignore` (`node_modules/`, `coverage/`), `README.md` (what it is, how to try it).
- `opencode/src/join/input.ts`: parse and check the tool input.
- `opencode/src/join/report.ts`: read a `<subagent>` report's state from its text.
- `opencode/src/join/record.ts`: what the plugin has seen, per parent and child.
- `opencode/src/join/wait.ts`: one `await_agents` call.
- `opencode/src/join/reminder.ts`: the reminder line.
- `opencode/src/plugin.ts`: default export `{ id, setup(ctx) }`, the 2.x adapter.
- `opencode/test/*.test.ts`: unit tests. `opencode/test/e2e/`: the fake-model end-to-end run.
- `.coven/tsconfig.base.json`, `.coven/eslint.config.mjs`: installed by `coven project`.
- `tests/test_dogfood.py`: `INSTALLED` gains the two files above.

---

### Task 1: Package scaffold and input checks

**Files:**
- Create: the `opencode/` project files above, `opencode/src/join/input.ts`
- Modify: `.coven/` (via `coven project`), `tests/test_dogfood.py` (`INSTALLED` list)
- Test: `opencode/test/input.test.ts`

**Interfaces:**
- Produces:
  `type Mode = "all" | "any"`;
  `interface AwaitInput { ids: string[]; mode: Mode; timeoutSeconds?: number }`;
  `class InputError extends Error`;
  `parseInput(raw: unknown, callerID: string): AwaitInput` (throws `InputError`);
  `const inputSchema` (the JSON Schema object the tool registers).

- [ ] **Step 1: Scaffold.** Create the project files; run `coven project` from the repo root so
  `.coven/` gets the TypeScript base files; add them to `INSTALLED` in `tests/test_dogfood.py`;
  `npm install` in `opencode/` to write the lockfile. Scripts: `"test": "node --test"`,
  `"e2e": "COVEN_E2E=1 unshare -rn node test/e2e/main.ts"`.
- [ ] **Step 2: Write the failing tests** in `opencode/test/input.test.ts`:
  - `parseInput({ids:["ses_a"]}, "ses_p")` deep-equals `{ids:["ses_a"], mode:"all"}`.
  - `mode:"any"` and `timeoutSeconds: 60` pass through.
  - Each throws `InputError` whose message names the bad value: `ids` missing, `[]`, 17 ids,
    `["ses_a","ses_a"]`, `["ses_p"]` (the caller), a non-string id, `mode:"some"`,
    `timeoutSeconds` `0`, `86401`, `1.5`, `"60"`.
- [ ] **Step 3: Run** `cd opencode && node --test test/input.test.ts`. Expected: FAIL, module not
  found.
- [ ] **Step 4: Implement** `parseInput` and `inputSchema` in `src/join/input.ts`. The schema
  mirrors the checks (`minItems 1`, `maxItems 16`, `uniqueItems`, `enum`, integer bounds,
  `additionalProperties: false`, `required: ["ids"]`).
- [ ] **Step 5: Run** the test file, then from the repo root
  `python3 .coven/check.py fast opencode/test/input.test.ts tests/test_dogfood.py`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(opencode): package scaffold and await_agents input checks`.

### Task 2: Report state and the record

**Files:**
- Create: `opencode/src/join/report.ts`, `opencode/src/join/record.ts`
- Test: `opencode/test/report.test.ts`, `opencode/test/record.test.ts`

**Interfaces:**
- Produces:
  `type ReportState = "running" | "completed" | "error" | "cancelled"`;
  `type EndState = "completed" | "error" | "cancelled" | "ended-without-report" | "removed"`;
  `reportState(text: string): ReportState | undefined` (reads `state="..."` from the
  `<subagent ...>` tag);
  `type Observed =
    | { kind: "started"; sessionID: string }
    | { kind: "ended"; sessionID: string }
    | { kind: "report"; parentID: string; childID: string; state: ReportState; description: string }
    | { kind: "removed"; sessionID: string }`;
  `interface ChildStatus { sessionID: string; description: string; end?: EndState; endedAt?: number }`;
  `class JoinRecord { constructor(now: () => number); observe(e: Observed): void;
    status(parentID: string, childID: string): ChildStatus; running(parentID: string): ChildStatus[];
    linkChild(parentID: string, childID: string, description: string): void; forget(): void }`.

Rules the record keeps (algorithm, not code): each observation gets a sequence number. A child's
`end` is the state of its newest report when that report is newer than its newest start and the
state is final (`completed`, `error`, `cancelled`). A `running` report newer than the newest
start means "still busy" (no end, and no no-report grace). `ended` with no report of any state
since the newest start sets `endedAt`; the wait turns that into `ended-without-report` after the
grace. `removed` is final at once. `forget()` drops parents whose last event is older than 24 h.

- [ ] **Step 1: Write the failing tests.**
  - `reportState('<subagent sessionID="ses_c" state="running" description="x">\nhi\n</subagent>')`
    is `"running"`; same for the three final states; plain text and a missing attribute give
    `undefined`.
  - Record: a `completed` report ends the child; `started` after that report clears the end; a
    `running` report after start leaves `end` undefined and `endedAt` undefined even after an
    `ended`; `ended` with no report sets `endedAt` to `now()`; `removed` gives `end: "removed"`;
    `running(parent)` lists linked children with no end; with a fake clock at +24 h + 1 ms,
    `forget()` drops the parent (its `running` is `[]`).
- [ ] **Step 2: Run** `node --test test/report.test.ts test/record.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `reportState` and `JoinRecord`.
- [ ] **Step 4: Run** the tests and `python3 .coven/check.py fast opencode/test/report.test.ts
  opencode/test/record.test.ts`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): join record and report state`.

### Task 3: One `await_agents` call

**Files:**
- Create: `opencode/src/join/wait.ts`
- Test: `opencode/test/wait.test.ts`

**Interfaces:**
- Consumes: `AwaitInput`, `JoinRecord`, `ChildStatus`, `EndState`, `reportState`.
- Produces:
  `interface HistoryItem { childID?: string; source?: string; text: string }`;
  `interface Host { getSession(id: string): Promise<{ id: string; parentID?: string } | undefined>;
    history(sessionID: string): Promise<HistoryItem[]>; lastAssistantText(sessionID: string): Promise<string>;
    now(): number; sleep(ms: number, signal: AbortSignal): Promise<void> }`;
  `interface CallContext { callerID: string; signal: AbortSignal; progress(status: string): void }`;
  `interface AwaitResult { mode: Mode; waitedSeconds: number;
    done: { sessionID: string; state: EndState; description: string; report?: string }[];
    pending: string[]; timedOut: boolean; note: string }`;
  `awaitAgents(input: AwaitInput, call: CallContext, record: JoinRecord, host: Host): Promise<AwaitResult>`;
  `class NotAChildError extends Error`, `class UnknownSessionError extends Error`.

Behaviour: check every id with `host.getSession` (unknown → `UnknownSessionError`, parent not the
caller → `NotAChildError`, a thrown lookup → an error naming the id) and `record.linkChild`. Read
history once at the start and every 15 s, feeding each item with `source === "subagent"` as a
report observation. Wake on record changes (an event emitter or a resolver the record calls) and
on the 15 s tick. Done when `all`: every id has an end; `any`: at least one. A child with
`endedAt` older than 10 s and no end becomes `ended-without-report` with
`report: await host.lastAssistantText(id)`; only that state carries `report`. Timeout returns
`timedOut: true` with the rest under `pending`. Abort rejects with `signal.reason`. Progress
every 30 s: `waiting for 2 of 3: child B, child C`. `note` is exactly
`Reports are in the conversation as <subagent> messages.` All timers are cleared on every exit.

- [ ] **Step 1: Write the failing tests** (fake `Host` with a fake clock and a manual `sleep`):
  - `all` over 3 ids ends only after the third final report; `done` lists 3, `pending` `[]`.
  - `any` ends on the first; a second `any` with the same ids returns at once with it in `done`.
  - A final report present only in history ends the wait before any event.
  - A report fed by history on the 15 s re-read (no event) ends the wait.
  - `ended` with no report → after 10 s, state `ended-without-report` with the last text.
  - `running` report then `ended` → still waiting after 60 s; the final report ends it.
  - `removed` mid-wait → state `removed`.
  - `timeoutSeconds: 30` → `timedOut: true`, the id under `pending`.
  - Abort with two pending → rejects with the abort reason; no `progress` call afterwards.
  - Progress after 30 s and 60 s with the exact text above.
  - Unknown id → `UnknownSessionError`; a session whose `parentID` is another → `NotAChildError`.
- [ ] **Step 2: Run** `node --test test/wait.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `awaitAgents`.
- [ ] **Step 4: Run** the test and `python3 .coven/check.py fast opencode/test/wait.test.ts`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): await_agents wait`.

### Task 4: Reminder and the OpenCode adapter

**Files:**
- Create: `opencode/src/join/reminder.ts`, `opencode/src/plugin.ts`
- Test: `opencode/test/reminder.test.ts`, `opencode/test/plugin.test.ts`

**Interfaces:**
- Consumes: everything above.
- Produces:
  `reminderText(running: ChildStatus[]): string | undefined`;
  `normalize(event: unknown): Observed | undefined`;
  `default { id: "coven.join", setup(ctx: PluginContext): Promise<() => void> }` where
  `PluginContext` is a local structural type of the 2.3.2 parts used: `event.subscribe({types,
  signal})`, `session.get`, `session.context`, `session.hook("context", fn)`,
  `tool.transform(fn)`.

Copy, exact: `reminderText` for two children is
`2 subagents are still running: child B (ses_b), child C (ses_c). Call await_agents with their ids before you finish or report.`
(one child: `1 subagent is still running: ...`); `undefined` for none.

`normalize` maps, with field paths read from the 2.3.2 schema (not guessed):
`session.execution.started` → `started`; `session.execution.succeeded`, `.failed`,
`.interrupted` → `ended`; `session.inbox.enqueued` whose item metadata `source` is `subagent` →
`report` (state from `reportState(item.payload.text)`, falling back to metadata `state`);
`session.deleted` → `removed`; anything else → `undefined`. Subscribe with exactly those `types`.
On a `started` event for a session not seen before, look it up once with `session.get` (cache
the answer, failures included) and, when it has a `parentID`, `record.linkChild(parentID, id,
title)`, so the reminder knows children the lead has not awaited yet. `plugin.ts` also builds the
`Host`: `history` maps `session.context` messages to `HistoryItem` (metadata `source` and
`childID`, the text), `lastAssistantText` takes the newest assistant message's text parts, `sleep`
is a timer cleared on abort. On stream end or error, resubscribe after 1 s, doubling to 30 s, reset on the first event. Call
`record.forget()` once an hour. The tool's description says it waits for final reports, returns
states only, and that after a server restart it is best effort.

- [ ] **Step 1: Write the failing tests.**
  - `reminderText` exact strings for 0, 1 and 2 children.
  - `normalize` on one recorded sample of each event type (copied from a real 2.3.2 run's SSE
    payloads into `test/fixtures/events.json`), and `undefined` for `message.part.delta`.
  - `setup` with a fake `ctx`: registers a tool named `await_agents` with `inputSchema`; the hook
    pushes the reminder only for a session with running children, including a child known only
    from a `started` event (looked up once: two `started` events give one `session.get`); a
    subscribe stream that ends
    is subscribed again (fake timers); the returned cleanup aborts the subscription.
- [ ] **Step 2: Run** `node --test test/reminder.test.ts test/plugin.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `reminder.ts` and `plugin.ts`.
- [ ] **Step 4: Run** the tests and `python3 .coven/check.py fast opencode/test/reminder.test.ts
  opencode/test/plugin.test.ts`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): join plugin adapter and reminder`.

### Task 5: End-to-end on the fake model

**Files:**
- Create: `opencode/test/e2e/main.ts`, `opencode/test/e2e/fake-model.ts`,
  `opencode/test/e2e/project/` (an `opencode.jsonc` with only the fake provider allowed, agents
  `lead` and `worker`, and `.opencode/plugins/coven-join/index.ts` re-exporting `src/plugin.ts`)
- Modify: `opencode/README.md` (how to run it)

Port the scripted fake model from the spike (`/tmp/coven-join/fake-model.mjs`, outside the repo;
read it, then write it fresh as TypeScript). Every file under `test/e2e/` has no top-level side
effects except `main.ts`, which does nothing unless `COVEN_E2E=1`, so `node --test` running it
passes as a no-op. `main.ts` checks it is in a namespace with only `lo` (else it exits 1 with
`not in a network namespace: run npm run e2e`), needs `COVEN_OPENCODE_BIN` pointing at a 2.3.2
binary (else it prints `skipped: set COVEN_OPENCODE_BIN` and exits 0), uses a temp HOME and XDG
dirs, starts the fake model and `opencode run --standalone`, and checks:
- `all` and `any` with 3 parallel workers both return;
- each child's `<subagent>` report appears exactly once in the lead's history;
- a worker that sleeps 120 s is waited for;
- a worker that spawns its own worker: the lead's wait ends only after the final report.

- [ ] **Step 1:** Write `main.ts` with the four checks failing (no plugin in the project yet).
- [ ] **Step 2: Run** `cd opencode && COVEN_OPENCODE_BIN=<pinned> npm run e2e`. Expected: FAIL.
- [ ] **Step 3:** Add the project plugin file and fix what the run shows.
- [ ] **Step 4: Run** it again. Expected: four `ok` lines. Save the output for the PR body.
- [ ] **Step 5: Run** `python3 .coven/check.py fast` with all `opencode/test/*.test.ts`.
  Expected: PASS.
- [ ] **Step 6: Commit** `test(opencode): end-to-end join on the fake model`.
