"""What `coven pm status` shows for each project-manager session.

A view joins the CLI's rows with the session's transcript. Everything printed is the first line
of a message the PM sent or the GM sent, cleaned. Tool results, thinking, tool inputs and the
text of a brief are never printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from coven import paths
from coven.pm.rows import (
    LIVE,
    PM_NAME_PREFIX,
    UNKNOWN_STATE,
    PmError,
    Row,
    pid_alive,
    pm_groups,
    row_state,
)
from coven.pm.text import (
    PM_PREFIXES,
    UNKNOWN_KEY,
    Head,
    clean_block,
    clean_line,
    parse_head,
)
from coven.pm.transcript import (
    EPOCH,
    Facts,
    Message,
    Received,
    Sent,
    find_transcript,
    read_transcript,
)

RECENT_DAYS = 7
STALE_AFTER = timedelta(hours=24)
# Read at call time, so tests can point them at a fake /proc and a fixed clock.
PROC_ROOT = Path("/proc")
WAIT_PREFIXES = ("QUESTION", "PROD-STEP", "DRAFT", "BLOCKED")
DONE_PREFIX = "DONE"
DUPLICATE = "duplicate"
STALE = "stale"
DELIVERED = "delivered"
NOT_DELIVERED = "NOT DELIVERED"
DRIFT_NOTE = "transcript format unknown: read its log"
NO_STATE = "unknown (no state)"
NO_SESSION_ID = "unknown (no session id)"
BAD_SESSION_ID = "unknown (bad session id)"
NO_TRANSCRIPT = "unknown (no transcript)"
# Also the state of a transcript with lines but no entry this reader knows: garbled or a new format.
UNREADABLE_TRANSCRIPT = "unknown (transcript unreadable)"
DRIFT_TURNS = 3  # assistant entries without one turn end that read as a format change
NO_GM_MESSAGE = "-"
UNKNOWN_SINCE = "?"
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Scene:
    """What a view is read against: the time now, the GM name the caller knows, and `/proc`."""

    now: datetime
    gm: str | None
    proc_root: Path


@dataclass(frozen=True)
class StatusRequest:
    codes: list[str]
    show_all: bool
    full: str | None
    gm: str | None


@dataclass(frozen=True)
class PmView:
    name: str
    ids: list[str]
    state: str
    age: timedelta | None
    since_gm: str
    last_out: Message | None
    last_out_flag: str | None
    last_in: Received | None
    waits: list[tuple[Head, datetime]]
    known_gm: str | None
    unsent: bool
    skipped: int
    drifted: bool
    live: bool  # a working, idle or blocked row whose process runs, and not judged stale
    has_pid: bool  # without one, `resume` takes a live-looking row as running until it is stopped


def live_rows(rows: list[Row], proc_root: Path) -> list[Row]:
    """The rows whose session is working, idle or blocked and whose process still runs."""
    return [
        row
        for row in rows
        if row_state(row) in LIVE and (row.pid is None or pid_alive(row.pid, proc_root))
    ]


def _is_stale(row: Row, facts: Facts | None, now: datetime, proc_root: Path) -> bool:
    if row.pid is not None:
        return not pid_alive(row.pid, proc_root)
    if row_state(row) != "working" or facts is None or facts.last_entry_at is None:
        return False
    return now - facts.last_entry_at > STALE_AFTER


def _format_drifted(facts: Facts) -> bool:
    """Whether the transcript no longer reads as the format this parser knows."""
    return facts.assistant_entries >= DRIFT_TURNS and not facts.turn_ends


def pm_state(
    rows: list[Row], facts: Facts | None, now: datetime, proc_root: Path
) -> tuple[str, list[str]]:
    """The state word of one PM name and the row ids it covers; `rows` are newest first.

    The word always comes from the rows: a drifted transcript is reported beside it, never instead.
    """
    live = live_rows(rows, proc_root)
    if len(live) > 1:
        return DUPLICATE, [row.id or UNKNOWN_KEY for row in live]
    newest = rows[0]
    ids = [newest.id or UNKNOWN_KEY]
    state = row_state(newest)
    if state in LIVE and _is_stale(newest, facts, now, proc_root):
        return STALE, ids
    return (NO_STATE if state == UNKNOWN_STATE else state), ids


def _gm_names(facts: Facts, gm: str | None) -> set[str]:
    """The names the GM wrote under: those a prompt names, `gm`, and any name the PM asked.

    A resumed PM's transcript can hold replies from an older GM name that no prompt in it names.
    """
    names = {prompt.gm_session for prompt in facts.prompts if prompt.gm_session}
    names |= {
        sent.to
        for sent in facts.sent
        if sent.message.head is not None
        and sent.message.head.prefix in PM_PREFIXES
        and sent.to != UNKNOWN_KEY  # the sender of a resume note that names no GM
    }
    return names | {gm} if gm else names


def _last_turn_age(facts: Facts, now: datetime) -> timedelta | None:
    moment = facts.turn_ends[-1] if facts.turn_ends else facts.last_entry_at
    if moment is None or moment == EPOCH:
        return None
    return max(now - moment, timedelta(0))


def _since_gm(facts: Facts, last_in: Received | None) -> str:
    """`?` whenever the turn ends cannot be trusted: `no` must never come from missing data."""
    if _format_drifted(facts):
        return UNKNOWN_SINCE
    if last_in is None:
        return NO_GM_MESSAGE
    sent_after = any(sent.message.at > last_in.message.at for sent in facts.sent)
    if not facts.turn_ends:
        return "message" if sent_after else UNKNOWN_SINCE
    if facts.turn_ends[-1] < last_in.message.at:
        return "no"
    return "message" if sent_after else "turn"


def _delivery_flag(sent: Sent) -> str:
    if sent.delivered:
        return DELIVERED
    if sent.delivered is None:
        return f"{NOT_DELIVERED}: no result seen"
    return f"{NOT_DELIVERED}: {sent.reason or 'the send failed'}"


def _last_out(facts: Facts) -> tuple[Message | None, str | None]:
    """The PM's last message and how it fared: a final text that was never sent counts."""
    text = facts.last_text
    if (
        text is not None
        and facts.last_text_after_send
        and text.head is not None
        and text.head.prefix in PM_PREFIXES
    ):
        return text, f"{NOT_DELIVERED}: never sent"
    if not facts.sent:
        return None, None
    last = facts.sent[-1]
    return last.message, _delivery_flag(last)


def _body_done_keys(message: Message) -> list[str]:
    """The keys of the `DONE` lines after a message's first line: a hand-over may end with one."""
    heads = (parse_head(line) for line in message.text.splitlines()[1:])
    return [key for head in heads if head and head.prefix == DONE_PREFIX for key in head.keys]


def _is_wait(head: Head, sent: Sent, done: dict[str, datetime], turn_ends: list[datetime]) -> bool:
    """Whether `sent`, with first line `head`, is a delivered ask the GM is to answer.

    Not one without a usable key, and not one sent in the turn of its task's `DONE`: that is the
    hand-over text for the user, which the GM never answers. A turn-end entry comes after the
    messages of its turn, so one stamped like the ask ends the ask's own turn, not an earlier one.
    """
    if head.prefix not in WAIT_PREFIXES or not sent.delivered or head.key == UNKNOWN_KEY:
        return False
    return head.key not in done or any(done[head.key] <= end < sent.message.at for end in turn_ends)


def _waits(facts: Facts, replies: list[Received]) -> list[tuple[Head, datetime]]:
    """The PM's last delivered asks that no later GM message or `DONE` answered, oldest first."""
    asked: dict[str, tuple[Head, datetime]] = {}
    done: dict[str, datetime] = {}
    for sent in facts.sent:
        head, at = sent.message.head, sent.message.at
        if head is not None and _is_wait(head, sent, done, facts.turn_ends):
            asked[head.key] = (head, at)
        closed = list(head.keys) if head and head.prefix == DONE_PREFIX else []
        for key in closed + _body_done_keys(sent.message):
            asked.pop(key, None)
            done[key] = at
    for reply in replies:
        for key in reply.message.head.keys if reply.message.head else ():
            if key in asked and reply.message.at > asked[key][1]:
                del asked[key]
    return sorted(asked.values(), key=lambda wait: wait[1])


def _known_gm(facts: Facts) -> str | None:
    named = [prompt.gm_session for prompt in facts.prompts if prompt.gm_session]
    return named[-1] if named else None


def build_view(
    name: str,
    rows: list[Row],
    facts: Facts | None,
    scene: Scene,
    missing: str | None = None,
) -> PmView:
    """The view of PM `name` from its `rows` (newest first) and transcript `facts`.

    Without `facts` the state is `missing`, the `unknown (...)` reason, unless the rows alone
    settle it as a duplicate or a dead process.
    """
    state, ids = pm_state(rows, facts, scene.now, scene.proc_root)
    has_pid = rows[0].pid is not None
    live = state != STALE and bool(live_rows(rows, scene.proc_root))
    if facts is None:
        if state not in (DUPLICATE, STALE):
            state = missing or NO_TRANSCRIPT
        return PmView(
            name=name,
            ids=ids,
            state=state,
            age=None,
            since_gm=NO_GM_MESSAGE,
            last_out=None,
            last_out_flag=None,
            last_in=None,
            waits=[],
            known_gm=None,
            unsent=False,
            skipped=0,
            drifted=False,
            live=live,
            has_pid=has_pid,
        )
    names = _gm_names(facts, scene.gm)
    from_gm = [received for received in facts.received if received.sender in names]
    last_in = from_gm[-1] if from_gm else None
    last_out, flag = _last_out(facts)
    undelivered = flag is not None and flag != DELIVERED
    answered = last_in is not None and last_out is not None and last_in.message.at > last_out.at
    return PmView(
        name=name,
        ids=ids,
        state=state,
        age=_last_turn_age(facts, scene.now),
        since_gm=_since_gm(facts, last_in),
        last_out=last_out,
        last_out_flag=flag,
        last_in=last_in,
        waits=_waits(facts, from_gm),
        known_gm=_known_gm(facts),
        unsent=undelivered and not answered,
        skipped=facts.skipped,
        drifted=_format_drifted(facts),
        live=live,
        has_pid=has_pid,
    )


def transcripts_dir() -> Path:
    """`${CLAUDE_CONFIG_DIR:-~/.claude}/projects`: one folder per working directory below it."""
    return paths.claude_dir() / "projects"


def _load_facts(projects_dir: Path, row: Row) -> tuple[Facts | None, str | None]:
    """The newest row's transcript, or why there is none (as an `unknown (...)` state)."""
    if not row.session_id:
        return None, NO_SESSION_ID
    try:
        found = find_transcript(projects_dir, row.session_id)
    except ValueError:
        return None, BAD_SESSION_ID
    except OSError:
        return None, UNREADABLE_TRANSCRIPT
    if found is None:
        return None, NO_TRANSCRIPT
    try:
        facts = read_transcript(found)
    except OSError:
        return None, UNREADABLE_TRANSCRIPT
    if facts.lines and not facts.recognized:
        return None, UNREADABLE_TRANSCRIPT
    return facts, None


def _is_recent_or_live(group: list[Row], now: datetime) -> bool:
    newest = group[0]
    started = newest.started_at
    return row_state(newest) in LIVE or (
        started is not None and now - started <= timedelta(days=RECENT_DAYS)
    )


def _selected(
    groups: dict[str, list[Row]], codes: list[str], show_all: bool, now: datetime
) -> list[str]:
    if codes:
        wanted = list(dict.fromkeys(f"{PM_NAME_PREFIX}{code}" for code in codes))
        for name in wanted:
            if name not in groups:
                raise PmError(f"no project-manager session named {clean_line(name)}")
        return wanted
    return [name for name, group in groups.items() if show_all or _is_recent_or_live(group, now)]


def views(
    projects_dir: Path,
    rows: list[Row],
    scene: Scene,
    codes: list[str],
    show_all: bool,
) -> list[PmView]:
    """One view per selected PM name: `codes` narrow the list, `show_all` lifts the recency cut."""
    groups = pm_groups(rows)
    built = []
    for name in _selected(groups, codes, show_all, scene.now):
        facts, missing = _load_facts(projects_dir, groups[name][0])
        built.append(build_view(name, groups[name], facts, scene, missing))
    return built


def clock(at: datetime) -> str:
    """`HH:MM` in the local zone; `?` for no stamp or one the local zone cannot hold."""
    if at == EPOCH:
        return "?"
    try:
        return at.astimezone().strftime("%H:%M")
    except (OverflowError, OSError, ValueError):  # year 1 or 9999 at an offset: out of range
        return "?"


def _age(age: timedelta) -> str:
    minutes = int(age.total_seconds() // 60)
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes}m"
    if minutes < MINUTES_PER_DAY:
        return f"{minutes // MINUTES_PER_HOUR}h"
    return f"{minutes // MINUTES_PER_DAY}d"


def status_lines(view: PmView) -> list[str]:
    """The block for one PM; a line with nothing to show is left out."""
    ids = ",".join(clean_line(identifier) for identifier in view.ids)
    state = clean_line(view.state)
    if view.age is not None:
        state = f"{state} {_age(view.age)}"
    lines = [f"{clean_line(view.name)}  id={ids}  state={state}  since GM: {view.since_gm}"]
    if view.last_out is not None:
        text = clean_line(view.last_out.text)
        lines.append(f"  last out: {clock(view.last_out.at)} {text}   [{view.last_out_flag}]")
    if view.last_in is not None:
        text = clean_line(view.last_in.message.text)
        sender = clean_line(view.last_in.sender)
        lines.append(f"  last in:  {clock(view.last_in.message.at)} {text}  (from {sender})")
    if view.waits:
        asks = ", ".join(f"{head.prefix} {head.key} since {clock(at)}" for head, at in view.waits)
        lines.append(f"  waits on GM: {asks}")
    if view.drifted:
        lines.append(f"  {DRIFT_NOTE}")
    if view.skipped:
        lines.append(f"  skipped lines: {view.skipped}")
    return lines


def summary_line(shown: list[PmView]) -> str:
    """`3 project managers, 2 live`: a PM is live when a row of it is working, idle or blocked.

    The row's process must still run and the state must not be stale; a duplicate (two such rows)
    counts once, and a missing or unreadable transcript does not change the count.
    """
    live = sum(view.live for view in shown)
    noun = "project manager" if len(shown) == 1 else "project managers"
    return f"{len(shown)} {noun}, {live} live"


def full_out(view: PmView) -> str:
    """The whole text of the PM's last message, cleaned; empty when it sent none."""
    if view.last_out is None:
        return ""
    return clean_block(view.last_out.text).rstrip("\n")


def status_report(rows: list[Row], request: StatusRequest) -> list[str]:
    """The lines `coven pm status` prints, with the clock and `/proc` read at call time."""
    scene = Scene(utcnow(), request.gm, PROC_ROOT)
    projects_dir = transcripts_dir()
    if request.full is not None:
        (view,) = views(projects_dir, rows, scene, [request.full], True)
        text = full_out(view)
        if not text:
            raise PmError(f"{clean_line(view.name)} has no message to show")
        return [text]
    shown = views(projects_dir, rows, scene, request.codes, request.show_all)
    if not shown:
        return ["no project-manager sessions"]
    return [*(line for view in shown for line in status_lines(view)), summary_line(shown)]
