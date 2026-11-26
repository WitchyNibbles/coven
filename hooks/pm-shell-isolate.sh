#!/bin/sh
# Run a PM's shell command in new user, PID and mount namespaces (OpenCode PM isolation).
# Usage: pm-shell-isolate.sh -c <command>
#
# OpenCode spawns `<shell> -c <command>` in the call's cwd; coven's plugin sets this script as the
# shell of a PM's calls and passes the real shell and the folders to hide in the environment:
#   COVEN_PM_REAL_SHELL  absolute path of the shell that runs the command
#   COVEN_PM_HIDE        folders to hide, `:`-separated; each that exists gets an empty tmpfs
#   COVEN_PM_PIDS        the folder (absolute) that holds this PM's pid records: just before the
#                        outer unshare replaces this process, the wrapper writes a file there named
#                        for its own pid, holding the process start time (field 22 of
#                        /proc/<pid>/stat). `coven_pm_stop` kills the trees of the records whose
#                        pid still has that start time, so a pid that was reused is never killed.
#   COVEN_ISOLATE_SYS    test seam: where the /proc/sys files used for the diagnosis are read
#                        (default /proc/sys). It moves those reads and nothing else: no value of it
#                        skips or weakens the unshare.
#   COVEN_ISOLATE_PATH   test seam: the trusted PATH the wrapper itself runs on (default
#                        /usr/sbin:/usr/bin:/sbin:/bin). Like COVEN_ISOLATE_SYS it is read from the
#                        environment only, and the plugin deletes both names from a PM shell's env,
#                        so a PM cannot set them.
#
# The wrapper never trusts the caller's PATH: a PM can put an `unshare` of its own ahead of /usr/bin
# (in ~/.local/bin, say) that drops the flags, and every later call would run unisolated. So the
# caller's PATH is kept aside, every tool is resolved on the trusted PATH, the outer and nested
# unshare run by absolute path, and the caller's PATH comes back only for the real shell.
#
# The command runs inside `unshare --user --map-root-user --pid --fork --kill-child --mount
# --mount-proc` (`--kill-child`: a kill of the process OpenCode spawned also kills the namespace), so its
# /proc shows only its own processes. There the folders are hidden, then a nested user namespace maps
# the caller's own uid and gid back (so files and git behave as before) and the real shell runs with
# the arguments untouched. The mounts of the outer namespace are locked for the inner one: the
# command cannot unmount the tmpfs or the new /proc. The command text only ever travels as an
# argument; no script text is built from it.
#
# Fail closed: where isolation is not available the script exits 126 with one line on stderr naming
# the first missing piece and never runs the command. There is no off switch.

caller_path=${PATH-}
PATH=${COVEN_ISOLATE_PATH:-/usr/sbin:/usr/bin:/sbin:/bin}
export PATH
[ -n "$caller_path" ] || caller_path=$PATH

refuse() {
    printf 'coven: PM shells run isolated and isolation is not available here: %s; ask the user\n' "$1" >&2
    exit 126
}

# One line from a diagnosis file, empty when the file is not there.
sys_value() {
    value=
    if [ -r "$sys_root/$1" ]; then
        read -r value < "$sys_root/$1" || true
    fi
    printf '%s' "$value"
}

# The start time of process $1: field 22 of /proc/$1/stat. The command name (field 2) may hold
# spaces and parentheses, so the fields are counted after the last closing parenthesis, where field 3
# comes first; field 22 is then the 20th. Prints nothing and fails when it is not a number.
start_time() {
    line=
    read -r line < "/proc/$1/stat" || return 1
    rest=${line##*) }
    skipped=0
    while [ "$skipped" -lt 19 ]; do
        rest=${rest#* }
        skipped=$((skipped + 1))
    done
    value=${rest%% *}
    case $value in
    '' | *[!0-9]*) return 1 ;;
    esac
    printf '%s' "$value"
}

real_shell=${COVEN_PM_REAL_SHELL-}
hide=${COVEN_PM_HIDE-}
pids=${COVEN_PM_PIDS-}
sys_root=${COVEN_ISOLATE_SYS:-/proc/sys}

[ -n "$real_shell" ] || refuse "COVEN_PM_REAL_SHELL is not set"
[ -n "$hide" ] || refuse "COVEN_PM_HIDE is not set"
[ -n "$pids" ] || refuse "COVEN_PM_PIDS is not set"
case $pids in
/*) ;;
*) refuse "COVEN_PM_PIDS is not an absolute path" ;;
esac
case $real_shell in
/*) ;;
*) refuse "COVEN_PM_REAL_SHELL is not an absolute path" ;;
esac
{ [ -f "$real_shell" ] && [ -x "$real_shell" ]; } ||
    refuse "COVEN_PM_REAL_SHELL is not an executable file"

unshare_bin=$(command -v unshare) || refuse "unshare not found"
sh_bin=$(command -v sh) || refuse "sh not found"
# Both run later by this name: a relative one would be searched on whatever PATH is then set.
case $unshare_bin in
/*) ;;
*) refuse "unshare is not an absolute path" ;;
esac
case $sh_bin in
/*) ;;
*) refuse "sh is not an absolute path" ;;
esac
for tool in id mktemp rm tr mount mv; do
    command -v "$tool" > /dev/null 2>&1 || refuse "$tool not found"
done
# `--map-user` came with util-linux 2.38; the help text says whether it is there.
case $("$unshare_bin" --help 2>&1) in
*--map-user*) ;;
*) refuse "util-linux is older than 2.38 (unshare has no --map-user)" ;;
esac
[ "$(sys_value user/max_user_namespaces)" != 0 ] ||
    refuse "user namespaces are off (max_user_namespaces is 0)"
[ "$(sys_value kernel/unprivileged_userns_clone)" != 0 ] ||
    refuse "user namespaces are off (unprivileged_userns_clone is 0)"
[ "$(sys_value kernel/apparmor_restrict_unprivileged_userns)" != 1 ] ||
    refuse "AppArmor blocks unprivileged user namespaces (apparmor_restrict_unprivileged_userns is 1)"

uid=$(id -u)
gid=$(id -g)

# The stage that runs inside the outer namespaces. It is a fixed text: the real shell, the ids, the
# folders, the unshare to run, the caller's PATH and the command arrive as arguments ($1 .. $6, then
# `-c <command>`), never inside it. It runs on the trusted PATH and gives the caller's back only to
# the real shell.
# shellcheck disable=SC2016
inner='
# This stage is the first process of the namespace, which ignores a TERM it has no handler for:
# trap the signals OpenCode may send, kill every process of the namespace and never go on.
trap '"'"'kill -s KILL -- -1; exit 143'"'"' TERM INT HUP
real=$1 uid=$2 gid=$3 hide=$4 unshare_bin=$5 caller_path=$6
shift 6
set -f
IFS=:
for dir in $hide; do
    [ -d "$dir" ] || continue
    if ! out=$(mount -t tmpfs tmpfs "$dir" 2>&1); then
        out=$(printf %s "$out" | tr "\n" " ")
        printf "coven: PM shells run isolated and isolation is not available here: cannot hide %s (%s); ask the user\n" "$dir" "$out" >&2
        exit 126
    fi
done
unset IFS
# Try the nested unshare on `true` first: a failure of it is told from a failing command.
if ! out=$("$unshare_bin" --user --map-user="$uid" --map-group="$gid" true 2>&1); then
    out=$(printf %s "$out" | tr "\n" " ")
    printf "coven: PM shells run isolated and isolation is not available here: cannot restore the user inside the namespaces (%s); ask the user\n" "$out" >&2
    exit 126
fi
exec 3<&0
PATH=$caller_path "$unshare_bin" --user --map-user="$uid" --map-group="$gid" "$real" "$@" <&3 3<&- &
wait "$!"
exit "$?"
'

# A preflight with the same flags, stderr captured, tells a failing unshare apart from a failing
# command and lets the real run be an exec: the process OpenCode spawned then is the unshare, and
# when OpenCode kills it (a timeout), `--kill-child` takes every process of the namespace with it.
errors=$(mktemp "${TMPDIR:-/tmp}/coven-pm-isolate.XXXXXX") || refuse "cannot create a temporary file"
trap 'rm -f "$errors"' EXIT
if ! "$unshare_bin" --user --map-root-user --pid --fork --kill-child --mount --mount-proc true 2> "$errors"; then
    message=$(tr '\n' ' ' < "$errors")
    refuse "unshare failed: ${message%"${message##*[! ]}"}"
fi
trap - EXIT
rm -f "$errors"

# The pid record: the exec below keeps this pid, so the outer unshare will be this process. A stop
# of the PM finds the namespace's tree by this file; the start time tells it the pid is still ours.
# The file is made whole before it has its name (a temp file, then a move), so a reader never
# sees half a record.
start=$(start_time "$$") || refuse "cannot read the start time of process $$"
record=$(mktemp "$pids/.record.XXXXXX" 2> /dev/null) || refuse "cannot write the pid record in $pids"
if ! { printf '%s\n' "$start" > "$record" && mv -f "$record" "$pids/$$"; } 2> /dev/null; then
    rm -f "$record"
    refuse "cannot write the pid record in $pids"
fi

exec "$unshare_bin" --user --map-root-user --pid --fork --kill-child --mount --mount-proc \
    "$sh_bin" -c "$inner" sh "$real_shell" "$uid" "$gid" "$hide" "$unshare_bin" "$caller_path" "$@"
