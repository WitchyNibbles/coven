---
description: Compact the current conversation into a handoff document for a fresh session to pick up. Use proactively when the conversation has grown long or context is filling up, before /clear or compaction, when switching to a different task, or when a work session is wrapping up.
---

<!-- Adapted from mattpocock/skills (MIT), see LICENSE and the repo's NOTICE. -->

Write a handoff document summarising the current conversation so a fresh agent can continue the
work. Save to `<store>/handoffs/<key>/<YYYY-MM-DD-HHMM>.md`. `<store>` is the memory store's path
from the "Shared memory" block at session start (or `memory_path` in
coven's `config.json` in the `coven` folder of the user's config folder, default `coven-memory` in the user's home folder). `<key>` is the folder name of the repo's
main checkout (a worktree uses its parent repo; outside a repo, the current folder's name). Create
the folder if needed. If the store does not exist, tell the user the memory store is not set up and print
the handoff in the reply instead.

Then commit it, and do not push: `git -C <store> add <file> && git -C <store> commit -m "handoff: <key> <YYYY-MM-DD-HHMM>"`.
Syncing is the `coven-shared-memory` rule (`coven memory sync` after a commit); this skill does not
push by itself. If the store's secret scan refuses the commit, remove the flagged text from the
file (never print it), commit again and tell the user. Never leave the handoff uncommitted (an
uncommitted file blocks every later sync of the store). Tell the user the file path and the
one-line prompt to start the next session with. A session opened in that project will see the
newest handoff in its "Shared memory" block.

Include a "suggested skills" section in the document, naming which skills the next agent should
call the `skill` tool for.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits,
diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable
information.

If the user passed arguments, treat them as a description of what the next session will focus on
and tailor the doc accordingly.

$ARGUMENTS
