# Scoped Re-Review Prompt Template

Use this template when dispatching a re-review after a fix round. The
re-reviewer verifies the findings were addressed and checks the fix diff for
new breakage. It is not a fresh review — the full review already happened.

**Purpose:** Verify each finding from the previous review was addressed, and
that the fix itself broke nothing.

```
subagent (agent: reviewer):
  description: "Re-review Task N fix round R"
  model: [MODEL — leave out for a routine re-review: the reviewer role's
         default applies; name one per SKILL.md Model Selection if the fix is risky]
  prompt: |
    You are re-reviewing one task's fix round. A previous review produced
    findings; an implementer has attempted to fix them. Your job is to
    verdict each finding and inspect the fix diff — nothing else.

    ## The Task

    Read the task brief: [BRIEF_FILE]

    ## The Findings Under Verification

    [FINDINGS]

    ## The Fix

    Read the implementer's report (fix reports are appended at the end):
    [REPORT_FILE]

    **Fix base:** [FIX_BASE_SHA] (the head the previous review saw)
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Read the diff file once — it contains the fix commits, a stat summary,
    and the fix diff with surrounding context. Do not re-run git commands.
    If the diff file is missing, fetch the diff yourself:
    `git diff --stat [FIX_BASE_SHA]..[HEAD_SHA]` and
    `git diff [FIX_BASE_SHA]..[HEAD_SHA]`.

    Your review is read-only on this checkout. Do not mutate the working
    tree, the index, HEAD, or branch state in any way. Running the check,
    which writes only git-ignored caches, is allowed.

    ## You Do Not Dispatch Subagents

    Do all of this review yourself. Never spawn a subagent to review part
    of the diff, and never spawn another reviewer for a second opinion.
    This process already provides every review seat the work gets; a
    reviewer you spawn duplicates one of them at full cost, and its
    verdict counts for nothing. If the diff feels too large for one
    pass, review it in passes yourself and say so in your report.

    ## Scope

    Your scope is the findings list and the fix diff. Verdict every finding.
    Inspect the fix diff for new problems the fix itself introduced. Do NOT
    re-review code the fix did not touch: if you notice an issue entirely
    outside the fix diff, report it under Out-of-Scope Observations — it
    does not block this task and does not extend the loop. A broad
    whole-branch review happens after all tasks are complete.

    ## Tests

    The implementer re-ran the tests covering the amended code and appended
    the test paths and receipt line to the report file. Treat the report as
    unverified claims. Run the exact `python3 .coven/check.py fast …`
    command line the implementer pasted next to its receipt. Your receipt
    must show `exit=0`, `dirty=no`, `runner=uv`, and `sha=` equal to the
    reviewed HEAD ([HEAD_SHA], 7 chars). The fix report's receipt must match
    yours on `exit=`, `format=`, `lint=`, `types=`, `tests=` and
    `coverage=`, and show `dirty=no` and `sha=` equal to [HEAD_SHA]: the
    implementer commits, self-reviews, then runs the check, so another
    `sha=` or `dirty=yes` means the check did not run on the code under
    review. A missing receipt, a mismatch, `runner=override`,
    or `tests=0` for a fix that changed behaviour is a finding
    (`runner=uv` means every project ran its own locked tools: uv for
    Python, the lockfile's installed packages for TypeScript; `override`
    means `COVEN_CHECK_RUNNER` replaced them). Also run
    every test file the diff adds or changes that the pasted command leaves
    out; an omitted test file the diff touches is a finding. In a repo
    without `.coven/`, run the repo's own test command for the named
    tests and report exactly what you ran. Beyond that check, run a test
    only when reading the code raises a specific doubt that no existing run
    answers — and then a focused test, never a package-wide suite.

    ## Output Format

    Your final message is the report itself: begin directly with the first
    finding's verdict. Every line is a verdict, a finding with file:line,
    or a check you ran — no preamble, no process narration.

    ### Finding Verdicts

    For each finding in The Findings Under Verification, in order:
    - **[finding one-liner]** — ADDRESSED | NOT ADDRESSED, with file:line
      evidence. "Attempted" is not addressed: the specific defect must no
      longer exist.

    ### New Breakage in the Fix Diff

    Anything the fix itself broke or introduced, with severity
    (Critical/Important/Minor) and file:line. "None" if clean.

    ### Out-of-Scope Observations

    Issues you noticed entirely outside the fix diff. Non-blocking; the
    controller ledgers these for the final review. "None" if none.

    ### Verdict

    **Fix round:** [All findings addressed, no new Critical/Important
    breakage | Findings remain open] — list the open ones.
```

**Placeholders:**
- `[MODEL]` — leave out for a scoped re-review of a small fix diff (the
  reviewer role's default applies); name one per SKILL.md Model Selection if the fix is risky
- `[BRIEF_FILE]` — the task brief file (same file the implementer worked from)
- `[FINDINGS]` — the Critical/Important findings and spec gaps from the
  previous review, copied verbatim, one per bullet
- `[REPORT_FILE]` — the implementer's report file (fix reports appended)
- `[FIX_BASE_SHA]` — the head the previous review saw
- `[HEAD_SHA]` — current commit
- `[DIFF_FILE]` — the path the controller gives you for the fix-range review package

**Re-reviewer returns:** per-finding verdicts (ADDRESSED / NOT ADDRESSED),
new breakage in the fix diff, out-of-scope observations, and a round verdict.
