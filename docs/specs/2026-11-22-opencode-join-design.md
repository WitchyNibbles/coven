# A join for OpenCode 2.x subagents

Status: design, 2026-11-22. First piece of coven on OpenCode 2.x (task coven-opencode/3). Starting
analysis: `docs/research/2026-11-22-coven-on-opencode.md`, section "Follow-up: a join for 2.x
subagents (spike)".

## Why

coven's way of working leans on one move: a lead dispatches subagents and waits for them before
going on (subagent-driven development: implementer, then reviewer, then fix). On Claude Code the
`Agent` tool blocks. On OpenCode 2.x the `subagent` tool always returns at once and the child runs
in the background (upstream #64073, #65316 open). The lead then often reports "done" while a child
still works, or moves on without the review. The spike showed a plugin tool can block for minutes
and that 2.x already sends each child's final report to the parent. This design turns that spike
into the real thing.

## Goals

- A tool `await_agents` that a lead calls after dispatching subagents. It returns when the given
  children have finished (`all`) or when the first of them has (`any`), with each one's state.
- No report twice: each child's report reaches the lead exactly once.
- The wait stops cleanly when the user interrupts, and can be bounded with a timeout.
- A lead that has running children is reminded of them before every model request, so it does not
  end its turn while they work.
- Tests on a fake local model only; nothing leaves the machine.

## Non-goals

- A push or watch tool. 2.x already wakes a parent whose turn ended with the child's report
  (spike, Q3), so a second wake path would only add duplicates.
- Waiting on sessions that are not children of the caller (a GM watching a PM in another
  session). That is the hierarchy milestone, over the server API.
- OpenCode 1.x. There `task` already blocks.
- Changing upstream OpenCode or the `subagent` tool.

## Anti-goals

- A lead that hangs forever on a child that died. Every wait ends: report, child error, interrupt,
  or timeout.
- Rewriting the lead's history in ways that break model switching (the `claude-agent` provider
  replays history; see its spec "History and Switching Models").

## How 2.x behaves today (receipts from the spike, 2.3.2)

- `subagent` returns `{sessionID, status: "running", output}` at once.
- When a child finishes a round, 2.x enqueues a synthetic message in the parent's inbox. The
  server event is `session.inbox.enqueued` with
  `data.sessionID = <parent>` and `data.item = {type: "synthetic", payload: {text:
  "<subagent sessionID=... state=... description=...>report</subagent>", description,
  metadata: {source: "subagent", childID, agent, state}}, delivery: "steer"}`.
- A `steer` item is delivered at the parent's next step boundary. If the parent is mid-tool, the
  report lands right after the tool result, in the same turn. If the parent is idle, it wakes it.
- Completion of a session shows as `session.execution.succeeded`; `session.idle` never came.
- A plugin sees every session's events on its server through `ctx.event.subscribe`.
- Plugins have `ctx.session.get`, `.context` (messages with their metadata), `.wait`, but no
  inbox list or cancel.

## Approaches

1. **Block on the child, return its report (the spike).** `ctx.session.wait` per child, then read
  the child's last assistant text and return it. Simple, but the native report also arrives, so the
  lead sees each report twice. Dedupe would mean a `context` hook deleting the native messages from
  every request, which edits history the `claude-agent` provider replays, and still leaves an extra
  wake when a report lands after the turn ended.
2. **Block on the parent's inbox, let the native report carry the text (picked).** The tool waits
  until each child's native `<subagent>` report has been enqueued for the caller, then returns only
  states. The report itself arrives once, through 2.x's own path, right after the tool result. No
  history edits, no extra wake, and it works for any child with a `parentID`, including ones
  created over the API.
3. **No tool, reminder only.** Rely on the native wake plus a `context` reminder. Cheapest, but the
  lead cannot express "wait for these two before reviewing", and the SDD loop stays turn-based.

Picked 2: it is the only one that gives a real barrier with each report exactly once.

## Design

### `await_agents`

Input:

```json
{ "ids": ["ses_..."], "mode": "all" | "any", "timeoutSeconds": 1800 }
```

- `ids`: 1 to 16 session ids, no duplicates.
- `mode`: default `all`.
- `timeoutSeconds`: optional, 1 to 86400. No timeout when absent.

Checks before waiting, each a tool error with a plain message:

- every id exists (`ctx.session.get`);
- every id's `parentID` is the calling session.

A child counts as **reported** when the plugin has seen, for the calling session, an inbox item
whose `metadata.source` is `"subagent"` and whose `metadata.childID` is that child, enqueued after
the child's most recent start. Sources, in order:

1. the plugin's own record, filled by one `ctx.event.subscribe` loop started in `setup` (every
   `session.inbox.enqueued` with that metadata, keyed by parent and child, with the item's
   `state`);
2. on each call, the caller's history (`ctx.session.context`): a message with that metadata
   counts, so a report that arrived before the plugin started (server restart) is not missed.

A child whose newest start comes after its newest report is running again and is waited for anew.

Only a **final** report ends a child's wait. 2.x sends a report with `state="running"` in its
`<subagent ...>` tag when the child finished its round but still has its own background work
(subagents, shells; `core/src/session/subagent-completion.ts`, `deliver`), while
`metadata.state` still says `completed`. So the state is read from the tag's `state` attribute:
`running` is progress (the lead sees it natively and the wait goes on), `completed`, `error` and
`cancelled` are final.

A child can also end a round with no report at all (no text and no background job, or a server
interrupt). So a child also counts as ended when its terminal execution event
(`session.execution.succeeded` or the failure and interrupt events, names read from the 2.3.2
schema) has come and no final report followed within 10 seconds. It is returned with state
`ended-without-report` and the text of its last assistant message, since no native report will
carry it.

The tool returns when:

- `all`: every id has reported;
- `any`: at least one id has reported (it returns every id that has, not just the first);
- timeout: the time ran out;
- interrupt: `context.signal` aborted, which ends the call with the abort error.

Result (text, JSON):

```json
{ "mode": "all", "waitedSeconds": 351.9, "done": [{ "sessionID": "ses_...", "state": "completed", "description": "child A" }], "pending": [], "timedOut": false, "note": "Each report follows as a <subagent> message." }
```

While waiting, `context.progress` every 30 s: `waiting for 2 of 3: child B, child C`.

Input errors, each a tool error that names the bad value: `ids` empty, over 16, or with a
duplicate; an id equal to the caller; `mode` not `all` or `any`; `timeoutSeconds` not an integer
from 1 to 86400. A server error from `ctx.session.get` is a tool error naming the id. A child
removed during the wait (`session.deleted`, name read from the schema) ends with state `removed`.

The record is a safety net, not the only path: while a call waits, it re-reads the caller's
history every 15 seconds, so a report whose event was missed (the subscription dropped, the
plugin restarted) still ends the wait. If the event subscription ends or throws, the plugin
subscribes again with a backoff from 1 to 30 seconds. The subscription asks only for the event
types it reads (`ctx.event.subscribe({types})`), not every token delta. The record forgets a
parent's entries 24 hours after their last event, so it does not grow without bound. The plugin
writes no files and no logs of its own.

The tool keeps no per-caller cursor. A second `any` over the same ids returns at once with the
children already done, so the lead passes only the ids still pending (the result lists them under
`pending`), and coven's SDD text says so. The note in the result says the reports are in the
conversation as `<subagent>` messages, which holds whether they arrived before or right after the
call.

Restart and compaction are best effort: after a server restart the plugin falls back to the
caller's history, and a report compacted out of that history is not found. Without a timeout a
wait can then last until the child's next report or the user's interrupt. This is written in the
tool's description.

### Reminder

A `ctx.session.hook("context", ...)` adds one system line when the session has children that are
running and have not reported:

```
2 subagents are still running: child B (ses_...), child C (ses_...). Call await_agents with their ids before you finish or report.
```

The plugin learns a session's running children from `session.execution.started` events for
sessions whose `parentID` it has looked up once (cached), and drops a child when its report is
recorded. No line when there are none. The hook never edits messages.

The reminder applies to every session on the server. That is coven's own pinned 2.x server with
its own config (user's choice 3a), so its sessions are coven's. The plugin does not rewrite the
`subagent` tool's own description ("end your response"); coven's agent prompts and SDD text carry
the rule to call `await_agents`, and the reminder names the tool.

### Where it lives

In this repo, in the second package `opencode/` next to the Claude Code plugin (user's choice,
2026-11-22: inside coven, sharing the Python core and CI). The plugin is a directory plugin
(`.opencode/plugins/coven-join/index.ts`); the M1 launcher installs it into coven's own pinned 2.x
setup later. It is provider-agnostic: nothing in it names a model or provider.

`opencode/` is a TypeScript project under the repo's code standard: `package.json`,
`package-lock.json`, `tsconfig.json` extending `.coven/tsconfig.base.json`, `eslint.config.mjs`
spreading `.coven/eslint.config.mjs`, dev dependencies eslint, prettier, typescript,
typescript-eslint, c8 and `@types/node`, tests under `opencode/test/` run by `node --test`. Adding
it makes `coven project` install the two TypeScript base files into `.coven/`, and the dogfood
test's list of installed files grows by those two (a test change made on purpose, named in the PR).

Code shape, so the logic is testable without OpenCode:

- `join.ts`: the record and the wait, over a small interface (`subscribe`, `getSession`,
  `history`, a clock). No OpenCode import.
- `plugin.ts`: the default export `{id, setup(ctx)}` that adapts `ctx` to that interface and
  registers the tool and the hook.

### Tests

- Unit tests with a fake event stream and fake sessions: all and any; a report recorded before the
  call; a report only in history; a child that restarts after reporting; timeout; abort; a wrong
  id; a non-child id; progress cadence (fake clock); the reminder text and its absence.
- One end-to-end test against OpenCode 2.3.2 with the scripted fake model from the spike, inside
  `unshare -rn` (only `lo`) and a provider policy that denies everything but the fake provider. It
  checks: 3 parallel children in `all` and `any`; each `<subagent>` report appears exactly once in
  the lead's history; a long wait (minutes) survives. It runs only where `unshare -rn` works and
  the pinned binary is present, and says so when it does not run. The scripted fake model moves
  from the spike into `opencode/test/e2e/`. CI runs the unit tests; the end-to-end test is run
  locally with its receipt in the PR, because CI runners may refuse unprivileged user namespaces.
- Cases for the end: a child with its own running subagent (a `running` report, then the final
  one); a child that ends with no report; a child removed mid-wait; a dropped subscription (the
  history re-read ends the wait); the record's 24-hour forgetting (fake clock); every input error.

## Left to the builder

- Package and tool layout details the repo's code standard imposes (lint, format, types, test
  runner).
- How the end-to-end test gets the pinned 2.3.2 binary without a global install.
- The exact event field paths, read from the 2.3.2 schema, not guessed.

## Not settled here

- Install and launch of coven on OpenCode: the next M1 PR (pinned 2.x in coven's own folder with
  its own XDG config dir, next to an untouched OpenCode 1.x; any OpenAI-compatible provider set in
  coven's own config, key read from a named env var and never stored).
- The hierarchy's cross-session wait.

## CEO review (2026-11-22, HOLD SCOPE)

Scope is the user's: M1 on 2.x, join tool first, provider-agnostic. Every section was read; the
findings are folded into the design above.

- Architecture: one event loop per plugin instance (2.x starts one per directory; each sees the
  whole server, so the caller's instance has what it needs). Added: resubscribe with backoff, a
  15 s history re-read while waiting, subscribing only to the needed event types, and a 24-hour
  limit on the record.
- Errors: every failure has a name and a message: input errors, unknown id, not a child, server
  error on lookup, abort, timeout (a result, not an error), `ended-without-report`, `removed`.
- Security: the tool only reads children of the caller, so a model cannot read other sessions
  through it. No secrets, no files, no network.
- Edge cases: empty, too many and duplicate ids; the caller's own id; a child that restarts; a
  child with its own children; a child that never reports; a child removed mid-wait.
- Code quality: the repo standard's limits (complexity 10, 300 lines a file) apply; split the
  record from the wait if a file grows past them.
- Tests: the unit list above, plus one local end-to-end run on the fake model.
- Observability: progress text every 30 s and a JSON result. A check that the plugin loaded at
  all belongs to the launcher and doctor (next M1 PR).
- State: in memory only, rebuilt from history after a restart; best effort, written in the tool
  description.
- API: one tool, `await_agents`, with a fixed input and result shape.
- Performance: the filtered subscription avoids reading every token delta; waits are event-driven
  plus one history read per 15 s per waiting call.
- UI: none beyond the tool's progress text.

Deferred, not in this PR: the launcher and installer, the plugin-loaded check, coven's SDD text
for OpenCode (it tells the lead to call `await_agents` and pass only pending ids), a cross-session
wait for the hierarchy.
