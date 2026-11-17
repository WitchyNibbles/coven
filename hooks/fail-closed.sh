#!/bin/sh
# Run a Claude Code hook so that a crash blocks the tool call instead of letting it through.
# Usage: /bin/sh fail-closed.sh <python-hook> [args]
#
# Claude Code blocks a call only on exit code 2: any other non-zero exit is a non-blocking error
# and the call goes ahead. A hook that fails before its own error handling runs (a syntax error, a
# missing module, a missing interpreter) would therefore fail open. The hook runs as
# `python3 -I launch.py <hook> [args]`, one interpreter per call, with stdin, stdout and stderr
# untouched; exit 0 (allow) and exit 2 (block) pass through, any other exit becomes 2. With no
# command at all, it blocks.
#
# launch.py (next to this file) checks the Python version first, before it reads stdin, and then
# runs the hook through the import system so that its bytecode is cached in the hooks folder: a
# second interpreter just to ask the version, and recompiling a script on every call, are what a
# call used to pay. It exits 86 for "too old" and turns a hook's own exit of 86 into 2.
#
# Hooks need Python 3.12 or later, and some systems ship an older python3 (macOS has 3.9) or none.
# On 86 (or with no python3 at all) deny_secrets.py falls back to a plain text check of the raw
# payload (below), prod_guard.py to a plain check of whether the guard is on (further below), and
# every other hook exits 0 (allowed, skipped). Either way the script prints, once per session, how
# to fix it. The payload is read only then: an old Python never touched stdin.
#
# prod_guard_gate.py, the entry hooks.json runs for the production guard, is the prod guard for
# this script: the same fallback and the same log name.
#
# Once-per-session marker: a file in ${XDG_CACHE_HOME:-$HOME/.cache}/coven/ named after the
# payload's session_id, read from the raw payload with sed (no JSON parser is available here).
# When the payload has no usable session_id (only letters, digits, `_` and `-`, at most 64
# characters) the marker is named after the date, so the message shows once a day instead.

NEED_PYTHON='coven: hooks need Python 3.12+; see README "Requirements"'

cache_root() {
    printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}/coven"
}

# Print NEED_PYTHON to stderr unless it was already printed for this session.
tell_once() {
    id=$(printf '%s' "$1" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]\{1,64\}\)".*/\1/p' | sed 1q)
    [ -n "$id" ] || id=$(date +%Y%m%d)
    marker="$(cache_root)/old-python-$id"
    [ -e "$marker" ] && return 0
    printf '%s\n' "$NEED_PYTHON" >&2
    mkdir -p "$(cache_root)" 2>/dev/null && : >"$marker" 2>/dev/null
    return 0
}

# Without a new enough python3: block a payload that names an env file or the ssh directory.
# The raw payload is searched as text, so `secrets.env` and `.environment` are blocked too; a
# `.env.example`, `.env.sample` or `.env.template` is allowed only as a whole name: one followed by
# a name character (a letter, digit, `_`, `.` or `-`), as in `.env.example.local`, still counts as
# an env file. A tool that fails (sed, or grep with an exit above 1) blocks too: an error is not
# "no match".
fallback_deny_secrets() {
    payload=$1
    tell_once "$payload"
    if ! stripped=$(printf '%s' "$payload" | sed -E 's/\.env\.(example|sample|template)([^A-Za-z0-9_.-]|$)/\2/g'); then
        printf '%s\n' "coven deny-secrets: could not check this call (sed failed); blocking" >&2
        return 2
    fi
    for name in .env .ssh; do
        printf '%s' "$stripped" | grep -F -q -e "$name"
        found=$?
        if [ "$found" -eq 0 ]; then
            printf '%s\n' "coven deny-secrets: access to '$name' is blocked (secrets)" >&2
            return 2
        fi
        if [ "$found" -gt 1 ]; then
            printf '%s\n' "coven deny-secrets: could not check this call (grep failed); blocking" >&2
            return 2
        fi
    done
    return 0
}

# Without a new enough python3 the prod guard cannot read its store, and skipping it would switch it
# off on exactly the machines where nobody sees it. So it refuses Bash and MCP tool calls, the ones
# that reach outside, while the guard is on, and lets every call through while it is off. "On" is
# the Python hook's own rule, read with sed and grep from the same config: a store folder
# (`memory_path`, `~/coven-memory` with no config file or key) and either `"guard": "required"` or a
# systems/guard.json in its working tree; with the latch a missing store folder still counts as on.
# This is a degraded mode, not the guard: it ignores the session's role, never reads the store's
# targets or approvals, and does not validate the config (a broken one is read best effort). That is
# acceptable because it only ever refuses more than the real guard would: the one thing it protects
# is that a machine with an old python3 does not quietly lose the guard, and the fix is to install
# Python 3.12+, which the message says.
guard_is_on() {
    config="${HOME}/.config/coven/config.json"
    # JSON allows a newline between a key, its colon and its value, and sed and grep read line by
    # line: the config is joined into one line first (a newline cannot occur inside a JSON string).
    text=
    if [ -f "$config" ]; then
        text=$(tr -d '\n\r' <"$config") || return 0 # unreadable: not knowing is not "off"
    fi
    store=$(printf '%s' "$text" | sed -n 's/.*"memory_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | sed 1q)
    [ -n "$store" ] || store="${HOME}/coven-memory"
    case $store in
    \~) store=$HOME ;;
    \~/*) store="${HOME}/${store#\~/}" ;;
    esac
    found=1
    if [ -f "$config" ]; then
        printf '%s' "$text" | grep -q '"guard"[[:space:]]*:[[:space:]]*"required"'
        found=$?
    fi
    [ "$found" -eq 1 ] || return 0 # the latch (or a grep that failed: not knowing is not "off")
    [ -d "$store" ] || return 1
    [ -e "$store/systems/guard.json" ] || [ -L "$store/systems/guard.json" ]
}

fallback_prod_guard() {
    payload=$1
    tell_once "$payload"
    guard_is_on || return 0
    printf '%s' "$payload" | grep -E -q '"tool_name"[[:space:]]*:[[:space:]]*"(Bash|mcp__[^"]*)"'
    found=$?
    [ "$found" -eq 1 ] && return 0
    printf '%s\n' "coven prod-guard: this python3 is too old to run the production guard; Bash and MCP tools are blocked until Python 3.12+ is installed" >&2
    return 2
}

# Append one line to the hook's log, unless it already holds 1 MB (the hooks rotate their own).
log_failure() {
    folder="$(cache_root)/logs"
    file="$folder/$1.jsonl"
    mkdir -p "$folder" 2>/dev/null || return 0
    if [ -f "$file" ] && [ "$(wc -c <"$file")" -ge 1048576 ]; then
        return 0
    fi
    printf '{"ts": "%s", "error": "exit %s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" >>"$file" 2>/dev/null
    return 0
}

if [ $# -eq 0 ]; then
    printf '%s\n' "coven: fail-closed.sh: no hook command" >&2
    exit 2
fi

hook=$1
name=${hook##*/}
name=$(printf '%s' "${name%.py}" | tr _ -)
[ "$name" = "prod-guard-gate" ] && name=prod-guard
case $0 in
*/*) here=${0%/*} ;;
*) here=. ;;
esac

# Old (launch.py exits 86 before reading stdin) or no python3: the fallbacks.
old_python_fallback() {
    payload=
    [ -t 0 ] || payload=$(cat)
    if [ "$name" = "deny-secrets" ]; then
        fallback_deny_secrets "$payload"
        exit $?
    fi
    if [ "$name" = "prod-guard" ]; then
        fallback_prod_guard "$payload"
        exit $?
    fi
    tell_once "$payload"
    exit 0
}

if ! command -v python3 >/dev/null 2>&1; then
    old_python_fallback
fi

python3 -I "$here/launch.py" "$@"
code=$?
case $code in
0 | 2) exit "$code" ;;
86) old_python_fallback ;;
esac
printf '%s\n' "coven: $name failed (exit $code); blocking" >&2
log_failure "$name" "$code"
exit 2
