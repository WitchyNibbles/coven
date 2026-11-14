#!/bin/sh
# The install smoke: what a stranger does on a fresh machine, with no model call and no API key.
#
# Installs the plugin from the checkout at $COVEN_SRC (default /src), runs the installed `coven`
# on the toy project the way the README does, and runs the toy's full check.
#
# Runs in the CI container (ci/install-smoke/Dockerfile), or anywhere else against an isolated
# HOME and CLAUDE_CONFIG_DIR that you set; it refuses to run without them, and refuses the
# default ~/.claude, so it cannot touch your own Claude Code.
set -eu

SRC=${COVEN_SRC:-/src}

log() {
    printf 'install-smoke: %s\n' "$*"
}

fail() {
    printf 'install-smoke: FAIL: %s\n' "$*" >&2
    exit 1
}

: "${HOME:?HOME must be set to an isolated directory}"
: "${CLAUDE_CONFIG_DIR:?CLAUDE_CONFIG_DIR must be set to an isolated directory}"
if [ "$CLAUDE_CONFIG_DIR" = "$HOME/.claude" ]; then
    fail "CLAUDE_CONFIG_DIR is $HOME/.claude, the default Claude Code folder; point it somewhere throwaway"
fi
[ -d "$SRC/.claude-plugin" ] || fail "$SRC is not the coven checkout (no .claude-plugin/)"
mkdir -p "$CLAUDE_CONFIG_DIR"

work=$(mktemp -d "${TMPDIR:-/tmp}/coven-smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT

found=$(claude --version)
log "claude: $found"
if [ -n "${COVEN_SMOKE_CLAUDE_VERSION:-}" ]; then
    case $found in
        "$COVEN_SMOKE_CLAUDE_VERSION"*) ;;
        *) fail "claude is $found, the job pins $COVEN_SMOKE_CLAUDE_VERSION" ;;
    esac
fi

log "claude plugin validate"
claude plugin validate "$SRC" --strict || fail "claude plugin validate --strict found problems"

# The checkout itself failing to add as a marketplace is a coven bug, never a fallback.
claude plugin marketplace add "$SRC" || fail "claude plugin marketplace add $SRC failed: the checkout must work as a marketplace"

# What may fall back: the official marketplace (a network fetch from GitHub) and the installs,
# which may need a login that CI does not have. A fresh Claude Code does not know the official
# marketplace yet, and superpowers lives there.
install_plugin() {
    claude plugin marketplace add anthropics/claude-plugins-official &&
        claude plugin install superpowers@claude-plugins-official &&
        claude plugin install coven@coven
}

mode=installed
if ! install_plugin; then
    if [ "${COVEN_SMOKE_REQUIRE_INSTALL:-0}" = 1 ]; then
        fail "plugin install failed and COVEN_SMOKE_REQUIRE_INSTALL=1 makes that fatal"
    fi
    # The job then proves only what `claude plugin validate` and the checkout's own bin/ prove;
    # docs/known-gaps.md says so.
    mode=fallback
    printf '%s\n' "::warning title=install smoke FALLBACK::plugin install failed, the install itself was NOT tested"
    log "FALLBACK: plugin install failed, so the install itself is NOT tested in this run"
    log "FALLBACK: using the checkout at $SRC in place of the installed plugin"
fi

plugins_file=${CLAUDE_CODE_PLUGIN_CACHE_DIR:-$CLAUDE_CONFIG_DIR/plugins}/installed_plugins.json
if [ "$mode" = installed ]; then
    root=$(python3 -I - "$plugins_file" <<'PY'
import json
import sys

entry = json.load(open(sys.argv[1], encoding="utf-8"))["plugins"]["coven@coven"]
print((entry[0] if isinstance(entry, list) else entry)["installPath"])
PY
    ) || fail "coven@coven is not in $plugins_file after the install"
    [ "$root" != "$SRC" ] || fail "the installed plugin is the checkout itself, not a copy"
else
    root=$SRC
fi
[ -x "$root/bin/coven" ] || fail "$root/bin/coven is missing or not executable"
coven=$root/bin/coven
log "coven: $coven ($mode)"

# --- coven doctor: no fail lines; warn lines only from the expected set --------------------------
# A fresh container has no gh login, no crossSessionInbound setting and no memory store yet.
doctor_rc=0
"$coven" doctor >"$work/doctor.txt" || doctor_rc=$?
cat "$work/doctor.txt"
[ -s "$work/doctor.txt" ] || fail "coven doctor printed nothing"
bad=0
while IFS= read -r line; do
    case $line in
        "ok "*) ;;
        "  "*) ;; # a detail line under a check
        "warn gh:"* | "warn crossSessionInbound:"* | "warn memory store:"* | "warn memory hook:"*) ;;
        "fail superpowers plugin:"*)
            # Only the fallback may lack superpowers: it never installed anything.
            if [ "$mode" = installed ]; then
                log "unexpected doctor line: $line"
                bad=1
            fi
            ;;
        *)
            log "unexpected doctor line: $line"
            bad=1
            ;;
    esac
done <"$work/doctor.txt"
[ "$bad" -eq 0 ] || fail "coven doctor reported something outside the expected warnings"
for check in "claude code" python3 git uv config; do
    grep -q "^ok $check:" "$work/doctor.txt" || fail "coven doctor did not report 'ok $check'"
done
if [ "$mode" = installed ]; then
    grep -q '^ok superpowers plugin:' "$work/doctor.txt" || fail "coven doctor did not report 'ok superpowers plugin'"
    [ "$doctor_rc" -eq 0 ] || fail "coven doctor exited $doctor_rc"
fi

# --- the README walkthrough on a copy of the toy -----------------------------------------------
# `coven project` wants the top of a git work tree, so the toy goes into a fresh repo first.
toy=$work/toy-demo
cp -r "$SRC/examples/toy" "$toy"
rm -rf "$toy/.venv" "$toy/.mypy_cache" "$toy/.pytest_cache" "$toy/.ruff_cache"
cd "$toy"
git init -q
git config user.name "coven install smoke"
git config user.email "smoke@example.invalid"
git add -A
git commit -q -m "toy"

"$coven" project .
# The one wiring `coven project` leaves to the reader: the toy's ruff config sits two folders up
# inside the coven repo, and in its own repo it reads the installed copy.
sed 's|^extend = "../../.coven/ruff.toml"$|extend = ".coven/ruff.toml"|' pyproject.toml >pyproject.new
mv pyproject.new pyproject.toml
grep -qx 'extend = ".coven/ruff.toml"' pyproject.toml || fail "could not point the toy's ruff config at .coven/ruff.toml"

"$coven" project . --check
uv sync
git add -A
git commit -q -m "install the standard"

python3 .coven/check.py full --base "$(git rev-list --max-parents=0 HEAD)" | tee "$work/check.txt"
grep -q '^CHECK full exit=0 .*dirty=no' "$work/check.txt" || fail "the toy's full check did not pass cleanly"

log "ok ($mode)"
