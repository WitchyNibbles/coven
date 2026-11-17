import re
from pathlib import Path

from support import SHIPPED_SKILLS

ROUTING = Path(__file__).resolve().parents[1] / "instructions" / "routing.md"
# Both plugins ship these two, so a bare name could start the wrong one.
CONTESTED = ("subagent-driven-development", "requesting-code-review")


def text() -> str:
    return ROUTING.read_text(encoding="utf-8")


def test_routing_under_10000_chars() -> None:
    assert 0 < len(text()) < 10_000


def test_routing_names_coven_sdd_by_full_name() -> None:
    assert "`coven:subagent-driven-development`" in text()
    assert "`coven:requesting-code-review`" in text()


def test_contested_skills_are_always_named_with_the_coven_prefix() -> None:
    for name in CONTESTED:
        bare = re.findall(rf"(?<!coven:)(?<![\w-]){re.escape(name)}", text())
        assert bare == [], f"{name} named without coven:"


def test_every_coven_name_is_a_skill_coven_ships() -> None:
    named = set(re.findall(r"coven:([a-z][a-z-]*)", text()))
    assert named <= SHIPPED_SKILLS


def test_routing_leaves_out_what_v1_cuts() -> None:
    lowered = text().lower()
    for cut in ("impeccable", "archify", "obsidian", "modernize", "docx"):
        assert cut not in lowered


def test_a_live_system_with_no_entry_goes_to_systems_map() -> None:
    bullets = text().split("\n- ")
    rule = next(bullet for bullet in bullets if "`coven:systems-map`" in bullet)
    assert "live system" in rule
    assert "systems/" in rule
