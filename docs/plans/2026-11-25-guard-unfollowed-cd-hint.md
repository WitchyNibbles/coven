# Guard: unfollowed-cd refusal hint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A zone refusal caused only by a `cd` the guard cannot follow says so and says what to do.

**Architecture:** `_zone_rule` in `hooks/prod_guard.py` already finds the zone a Bash call
touches. When one does, it asks once more with every `cd` treated as followable; if that answer
is "does not touch", the refusal gets the hint sentence. What is refused does not change.

**Tech Stack:** Python 3.12+, pytest, `python3 .coven/check.py`.

**Spec:** `docs/specs/2026-11-25-guard-unfollowed-cd-hint-design.md`

## Global Constraints

- Allow/refuse decisions do not change: every existing test in `tests/hooks/` passes unchanged.
- The hint sentence, verbatim: ``After a `cd` the guard cannot follow (`cd $VAR`), a relative path
  or glob such as `*.json`, given to a program that is not a reader, may land in or match a name
  in the zone: use `cd /literal/path` or absolute paths.`` It is appended after the existing
  refusal text, separated by `". "` (the existing text has no final period).
- Test commands are synthetic (`/srv/work/wt-x`, `/srv/in`), never a real machine path or user
  name.
- No new dependency, no suppression, no weakened lint or type rule. No Co-Authored-By trailer or
  "Generated with" line.
- The live guard reads every Bash command you run. A command whose text holds a refused pattern
  is refused for you too: put such text in files with the Write/Edit tools (tests are files) and
  run `coven guard explain --file <scratch>` on it. Never rebuild a refused command another way
  (character codes, string concatenation, base64, a generated script) to get past the guard; if
  you need such a run, stop and report it.

## Review Focus

1. A refusal with another cause (`cd ~/.claude && touch x`, `rm ~/.claude.json`) gets no hint.
   Test in Task 1.
2. A read after an unfollowed `cd` (`cd $W && cat docs/x/*.json`) stays allowed. Test in Task 1.
3. The literal-`cd` twin of the refused text stays allowed. Test in Task 1.
4. The hint appears for a relative name, not just a glob (`cd $W && rm .claude.json`). Test in
   Task 1.
5. Other zones (the memory store) get the hint only under the same condition. Covered by the
   counterfactual being zone-generic; no extra test.

---

### Task 1: Hint for refusals caused by an unfollowed cd

**Files:**
- Modify: `hooks/prod_guard.py` (`_bash_touches`, `_zone_refusal`, `_zone_rule`)
- Modify: `docs/known-gaps.md` ("More false positives for the user folder" list, around l.173)
- Test: `tests/hooks/test_prod_guard.py` (next to
  `test_a_restricted_session_cannot_change_claude_codes_user_folder`, ~l.4353)

**Interfaces:**
- `_bash_touches(call: Call, zone: _Zone, *, follow_cds: bool = False) -> bool`: with
  `follow_cds=True`, `_Where.cds_clear` is True whatever `_cds_clear` says; nothing else changes.
- `_zone_refusal(zone: _Zone, role: str, *, unfollowed_cd: bool = False) -> Refused`: appends the
  hint when `unfollowed_cd`.
- `_zone_rule`: on a touch, `unfollowed_cd = not _bash_touches(<same call>, zone, follow_cds=True)`.

- [ ] **Step 1: Write the failing tests** (use the file's `run`, `bash`, `home()` helpers; role `pm`)

```python
UNFOLLOWED = (
    "W=/srv/work/wt-x; mkdir -p $W/docs/x && cp /srv/in/*.json $W/docs/x/ && cd $W"
    " && uv run privacy-scan docs/x/*.json > /tmp/scan.out 2>&1"
)
HINT = "After a `cd` the guard cannot follow (`cd $VAR`)"

def test_a_glob_after_a_cd_the_guard_cannot_follow_is_refused_with_why(run, store):
    proc = run(bash(UNFOLLOWED), role="pm")
    assert proc.returncode == 2
    assert "cannot write to Claude Code's user folder" in proc.stderr
    assert HINT in proc.stderr
    assert "use `cd /literal/path` or absolute paths." in proc.stderr

def test_the_same_text_with_a_literal_cd_is_allowed(run, store):
    proc = run(bash(UNFOLLOWED.replace("cd $W", "cd /srv/work/wt-x")), role="pm")
    assert proc.returncode == 0, proc.stderr

def test_a_reader_after_a_cd_the_guard_cannot_follow_is_allowed(run, store):
    proc = run(bash("W=/srv/work/wt-x; cd $W && cat docs/x/*.json"), role="pm")
    assert proc.returncode == 0, proc.stderr

def test_a_relative_name_after_a_cd_the_guard_cannot_follow_gets_the_hint(run, store):
    proc = run(bash('cd "$X" && rm .claude.json'), role="pm")
    assert proc.returncode == 2 and HINT in proc.stderr

@pytest.mark.parametrize("command", ["cd ~/.claude && touch x", "rm ~/.claude.json"])
def test_a_refusal_with_another_cause_has_no_hint(run, store, command):
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2 and HINT not in proc.stderr
```

Adjust only the helper calls to what the file really uses (e.g. `home()`-based paths for the
`~/.claude` cases if `~` is not the test home); keep the assertions.

- [ ] **Step 2: Run them to see them fail**

Run: `python3 .coven/check.py fast tests/hooks/test_prod_guard.py`
Expected: the two hint tests FAIL (no hint in stderr); the allowed and no-hint tests pass.

- [ ] **Step 3: Implement** the three interface changes above in `hooks/prod_guard.py`. The
  counterfactual call runs only after a touch was found, so allowed calls cost nothing extra.

- [ ] **Step 4: known-gaps line.** Under "More false positives for the user folder", add one
  item: a write after a `cd` the guard cannot follow (`W=/x; cd $W && uv run tool d/*.json`): a
  relative glob such as `*.json` may match `~/.claude.json`; the refusal says so; use
  `cd /literal/path` or absolute paths.

- [ ] **Step 5: Run the tests to see them pass**

Run: `python3 .coven/check.py fast tests/hooks/test_prod_guard.py`
Expected: all pass, receipt line printed.

- [ ] **Step 6: Commit**

```bash
git add hooks/prod_guard.py tests/hooks/test_prod_guard.py docs/known-gaps.md
git commit -m "fix(guard): say why a write after an unfollowed cd is refused"
```
