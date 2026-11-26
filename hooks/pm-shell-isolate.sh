#!/bin/sh
# Run a PM's shell command in new user, PID and mount namespaces (OpenCode PM isolation).
# Usage: pm-shell-isolate.sh -c <command>
#
# OpenCode spawns `<shell> -c <command>` in the call's cwd; coven's plugin sets this script as the
# shell of a PM's calls and passes the real shell and the folders to hide in the environment:
#   COVEN_PM_REAL_SHELL  absolute path of the shell that runs the command
#   COVEN_PM_HIDE        folders to hide, `:`-separated; each that exists gets an empty tmpfs
#   COVEN_ISOLATE_SYS    test seam: where the /proc/sys files used for the diagnosis are read
#                        (default /proc/sys). It moves those reads and nothing else: no value of it
#                        skips or weakens the unshare.
#
# The command runs inside `unshare --user --map-root-user --pid --fork --mount --mount-proc`, so its
# /proc shows only its own processes. There the folders are hidden, then a nested user namespace maps
# the caller's own uid and gid back (so files and git behave as before) and the real shell runs with
# the arguments untouched. The mounts of the outer namespace are locked for the inner one: the
# command cannot unmount the tmpfs or the new /proc. The command text only ever travels as an
# argument; no script text is built from it.
#
# Fail closed: where isolation is not available the script exits 126 with one line on stderr naming
# the first missing piece and never runs the command. There is no off switch.

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

real_shell=${COVEN_PM_REAL_SHELL-}
hide=${COVEN_PM_HIDE-}
sys_root=${COVEN_ISOLATE_SYS:-/proc/sys}

[ -n "$real_shell" ] || refuse "COVEN_PM_REAL_SHELL is not set"
[ -n "$hide" ] || refuse "COVEN_PM_HIDE is not set"
case $real_shell in
/*) ;;
*) refuse "COVEN_PM_REAL_SHELL is not an absolute path" ;;
esac

command -v unshare > /dev/null 2>&1 || refuse "unshare not found"
# `--map-user` came with util-linux 2.38; the help text says whether it is there.
case $(unshare --help 2>&1) in
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
# folders and the command arrive as arguments ($1 .. $4, then `-c <command>`), never inside it.
# File descriptor 3 holds the wrapper's own stderr: the outer unshare's stderr is captured to tell a
# failed unshare from a command that failed, and this stage hands the real one back first.
# shellcheck disable=SC2016
inner='
exec 2>&3 3>&-
real=$1 uid=$2 gid=$3 hide=$4
shift 4
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
exec unshare --user --map-user="$uid" --map-group="$gid" "$real" "$@"
'

errors=$(mktemp "${TMPDIR:-/tmp}/coven-pm-isolate.XXXXXX") || refuse "cannot create a temporary file"
trap 'rm -f "$errors"' EXIT

unshare --user --map-root-user --pid --fork --mount --mount-proc \
    sh -c "$inner" sh "$real_shell" "$uid" "$gid" "$hide" "$@" 3>&2 2> "$errors"
status=$?

# Nothing else writes to the captured stream: the stage restores stderr before it does anything.
if [ "$status" -ne 0 ] && [ -s "$errors" ]; then
    message=$(tr '\n' ' ' < "$errors")
    refuse "unshare failed: ${message%"${message##*[! ]}"}"
fi
exit "$status"
