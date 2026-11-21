# aquelarre

A Claude Code mod that shows the live coven project managers and what each one is doing. It is the
second plugin in the coven marketplace and installs with coven.

## What it shows

- **A pane.** `/aquelarre` opens a pane beside the chat: one card per live project manager, with its
  state, how long it has been in it, the plan it is working through, what it waits on and the last
  messages it sent and received.
- **A band** above the prompt: one row that sums up how many project managers are working or
  waiting, dim when no one is working.
- **Toasts** when a project manager asks a question, needs a production step, finishes or dies.

The text is Spanish for now; an English default and a language setting come later.

## Where it reads from

- `coven pm status`, run every few seconds while a window is open. When `coven` is missing or fails,
  the pane and the band show the error and keep the last cards.
- The plan of each project manager, from the task folders under the Claude Code config directory
  (`CLAUDE_CONFIG_DIR`, else `.claude` under `HOME`).
- A small cache under `XDG_CACHE_HOME` (else `.cache` under `HOME`) so that several windows share one
  read instead of each running the command.

A project manager's own session (`COVEN_SESSION_ROLE` set) never reads or draws any of this.

## Develop

The code is TypeScript in `hooks/register.tsx`, wired by `hooks/hooks.json`. Its tests run with the
mod test kit:

```bash
claude plugin test plugins/aquelarre
claude plugin validate --strict plugins/aquelarre
```

`validate` also reports TypeScript errors, so it is the type check. `python3 .coven/check.py fast
<path inside the mod>` runs both.
