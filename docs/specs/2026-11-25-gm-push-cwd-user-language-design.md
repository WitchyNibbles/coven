# Two GM fixes: `cd <dir> && git push`, and the user's language

Found while running the GM on 2026-11-25.

## 1. pm-permissions refuses `cd <worktree> && git push`

### What happened

A PM ran `cd <worktree> && git push 2>&1 | tail -n 2`. The worktree was on a feature branch
with an upstream of the same name. The hook payload's `cwd` was the session's main checkout, on
`main`. The hook refused: `pushing to main (protected: ...)`. The same `cd <worktree> && git push
-u origin <branch>` passed earlier, because a named branch is checked by name.

Receipt: the PM's session transcript holds that exact command, and every hook call in it carries
the main checkout as `cwd`.

### Cause

`_plan` in `hooks/pm_permissions.py` keeps a list of candidate directories. `_after_cd` adds the
`cd` target but keeps the old directories too, because "the cd can fail". That is right after `;`
(the push still runs when the cd failed) but wrong after `&&`: when `cd` fails, nothing joined to
it by `&&` runs. So the main checkout stayed a candidate, its current branch `main` was among the
branches a plain push would update, and the push was refused.

`git -C <dir> push` already replaces every candidate with `<dir>` joined onto it, and a plain push
with the payload's `cwd` in the worktree already checks that worktree. Both need tests, not code.

### Fix

Track, next to the set of every directory a segment may run in, the directories a segment runs in
when it is reached through an unbroken `&&` chain from the start of its list. This applies only to a
command without a parenthesis; a command with `(` or `)` is never narrowed (see the last bullet).

- A segment reached by `&&`, with only `&&` between it and the start of the list (the command's
  start, or the last `;`), runs in the directories the previous segment ran in, moved by that
  segment when it was a `cd`.
- Any other segment (the first one, one after `;`, or anything after `||` or `|` in the same list)
  runs in every directory any segment so far may have reached: today's behaviour.
- `||` and `|` taint the rest of the list up to the next `;`: `git status || cd X && git push` may
  push from the old directory when `git status` succeeds.
- When the command holds any parenthesis, nothing is narrowed: every segment runs in every
  directory any segment so far may have reached. A subshell's `cd` ends at its `)`, and a `;`
  inside it would otherwise reset the chain: `(git status; cd X) && git push` pushes from the old
  directory.

A push whose branch cannot be resolved in a directory it may run in is still refused: a missing
`cd` target, a directory that is not a repo, a detached HEAD.

### Tests (written first)

In `tests/hooks/test_pm_permissions.py`, with a worktree made by `git worktree add` from the
`repo` fixture:

- plain `git push` with the payload's `cwd` in a worktree on a feature branch with an upstream:
  allowed;
- plain `git push` with the payload's `cwd` in a worktree on `main`: refused, "pushing to main";
- `git -C <worktree> push` with the payload's `cwd` in the main checkout on `main`: allowed;
- `cd <worktree> && git push` and `cd <worktree> && git push 2>&1 | tail -n 2` from the main
  checkout on `main`: allowed (fails today);
- `cd <worktree on main> && git push` from a checkout on a feature branch: refused;
- `cd <worktree>; git push` from the main checkout on `main`: refused (the cd may fail);
- `git status || cd <worktree> && git push` from the main checkout on `main`: refused;
- `(cd <worktree>) && git push` from the main checkout on `main`: refused;
- `cd <missing> && git push`: refused;
- the existing tests at `test_a_cd_that_may_fail_leaves_the_old_directory_a_candidate` and
  `test_cd_and_git_dash_c_directories_may_not_hide_a_second_directory` keep passing.

## 2. The GM answers the user in English

### What happened

The user writes in Spanish. After a compaction the GM answered in English for many turns, pulled
by English PM messages, briefs and the compaction summary. The only rule is one line near the end
of `skills/manager/SKILL.md` ("Talk to the user in the language they wrote in. Briefs and replies
to PMs are in English."). The routing (`instructions/routing.md`), which `hooks/session_context.py`
injects on every start, resume, clear and compaction, has no language rule.

### Fix

- `instructions/routing.md`, "Talking to people": one paragraph. Reply to the user in the
  language they write in, from the first line, also after a compaction or a resume, even when
  briefs, agent messages, tool output or the summary around it are in another language. It covers
  every reply, question, production-step summary, report and the text around a draft. Text for
  agents (briefs, subagent prompts, replies to PMs) stays in English. Repo text (code, commits,
  PRs, docs) follows the repo's language. A draft for someone else follows its reader. Because the
  routing is re-injected on `compact`, this is the post-compaction reminder; no new hook.
- `skills/manager/SKILL.md`: the rule moves from "Always" to the top of the skill, right after the
  opening paragraph, with what it covers for the GM: relaying a PM's `QUESTION`, `PROD-STEP`,
  `DRAFT` or `DONE` means saying it in the user's language (commands, paths, error text and the
  draft's own text stay verbatim). "Always" keeps a one-line pointer.
- The brief (`skills/manager/SKILL.md`, "The brief, in English") gains the user's language, so a
  PM's `DRAFT` and a resumed GM know it without guessing.
- `agents/project-manager.md`: `DRAFT` already says "in the language of the hand-over"; it
  stays, and the PM's messages to the GM stay English. Checked consistent; no change unless the
  brief field needs naming there.
- `skills/handoff/SKILL.md` and its OpenCode override: the handoff names the user's language, so
  the next session starts in it.
- OpenCode: `opencode/overrides/routing/AGENTS.md` gets the same paragraph, and
  `opencode/content/` is regenerated with `python -m coven.opencode.content`. OpenCode's
  `AGENTS.md` is part of every request, so it survives compaction there too. The manager skill is
  not shipped on OpenCode yet (M2 is on its own branch), so nothing more is needed there now.

### Tests (written first)

Text tests in the style of `tests/test_routing_text.py`, `tests/test_skills_text.py` and the
OpenCode content tests under `tests/oc/`: the routing's "Talking to people" section holds the
rule; the manager skill states it before its first `##` section; the brief lists the user's
language; the handoff skill asks for it; the OpenCode routing holds the same sentence.
