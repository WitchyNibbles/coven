# Guard: say why a write after an unfollowed `cd` is refused

## Problem

A PM's command was refused with the user-folder refusal ("cannot write to Claude Code's user
folder") though it touched nothing under `~/.claude`:

```sh
W=/srv/work/wt-x; mkdir -p $W/docs/x && cp /srv/in/*.json $W/docs/x/ && cd $W && uv run privacy-scan docs/x/*.json > /tmp/scan.out 2>&1
```

Bisected with `coven guard explain --file`: the refusal comes from `cd $W` (a `cd` whose target
the guard cannot follow, since its operand holds `$`) followed by a relative glob (`docs/x/*.json`)
given to a program that is not a reader (`uv run ...`). After such a `cd` every relative glob part
is matched against the zone's names (`hooks/prod_guard.py` `_expanded_text_reaches` ->
`_relative_glob_names_zone` -> `_part_names_zone`), and `*.json` matches `.claude.json`. The same
text with `cd /srv/work/wt-x` is allowed. A reader (`cat docs/x/*.json`) or no glob is allowed
too.

The rule is right to refuse: the guard cannot see where `$W` points or whether `docs` is a
symlink to `~`. The refusal just does not say so, and the PM cannot tell what to change.

## Decision (the user's choice, option (b))

- Keep the rule. Nothing that is refused today becomes allowed, and nothing allowed becomes
  refused. No new trust in `cd $VAR` or in same-command assignments.
- When a zone refusal happens only because of a `cd` the guard cannot follow, the refusal adds
  this sentence after the usual text:

  > After a `cd` the guard cannot follow (`cd $VAR`), a relative glob such as `*.json`,
  > given to a program that is not a reader, may match a name in the zone: use
  > `cd /literal/path` or an absolute glob.

- "Only because of": the same call, with every `cd` in it treated as one the guard can follow,
  would not be refused by that zone. A refusal with another cause (a word naming the zone, a
  literal `cd ~/.claude`, a write to `~/.claude.json`) keeps today's text.
- A relative name after an unfollowed `cd` (`cd $X && rm .claude.json`) is refused by another
  check (`_changes_zone_holder` via `_after_cd`) and keeps the plain refusal.
- Only the user-folder zone gets the hint: it is the only zone that follows a literal `cd`.
- The hint is not added when the session starts in a folder that holds the zone: a literal `cd`
  would not help there. After `..`, `popd` or `cd ~` it may still show, and its absolute-glob
  remedy still works there.
- `docs/known-gaps.md` lists the case under "More false positives for the user folder".

## Not in scope

- Resolving `W=/literal; cd $W` to a known folder (option (a)): declined by the user.
