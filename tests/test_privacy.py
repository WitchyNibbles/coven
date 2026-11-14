"""Generic leak guard: no real user names in home paths and no stray email addresses in tracked files.

Names of private organisations and systems are not listed here (the list itself would leak them);
the owner's local pre-commit scan covers those.
"""

import re
import subprocess

import pytest
from support import REPO

# The name after a users folder, ended by anything that cannot be part of a name (`/`, `:`, a quote,
# the end of the text): `<user>`, `$USER` and a lone `/home/` never match.
NAME = r"([A-Za-z0-9][A-Za-z0-9._-]*)(?![\w.-])"
POSIX_HOME = re.compile(rf"((?:/home|/Users|/mnt/c/Users))/{NAME}", re.IGNORECASE)
WINDOWS_HOME = re.compile(rf"[A-Za-z]:\\+Users\\+{NAME}", re.IGNORECASE)
WSL_SHARE_HOME = re.compile(
    rf"\\\\+wsl(?:\$|\.localhost)\\+[^\\\s/]+\\+home\\+{NAME}", re.IGNORECASE
)
PLACEHOLDER_NAMES = {"user", "runner"}
# The system's shared profile on Windows drives; there is nobody's name in it.
SHARED_PROFILE = "public"
EMAIL = re.compile(r"([A-Za-z0-9._%+\[\]-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
PLACEHOLDER_DOMAINS = ("example.com", "example.invalid")
# The vendors whose noreply co-author addresses the attribution check's tests use.
VENDOR_DOMAINS = ("anthropic.com", "openai.com", "opencode.ai", "github.com")
NOREPLY_LOCALS = {"noreply", "no-reply"}
FORGE_NOREPLY = "users.noreply.github.com"


def under(domain: str, parent: str) -> bool:
    return domain == parent or domain.endswith("." + parent)


def email_allowed(local: str, domain: str, author_email: str, author_handle: str) -> bool:
    local, domain = local.lower(), domain.lower().rstrip(".")
    if f"{local}@{domain}" == author_email.lower():
        return True
    if domain.endswith(".test") or any(under(domain, d) for d in PLACEHOLDER_DOMAINS):
        return True
    if domain == FORGE_NOREPLY:
        # `<name>[bot]`, or `<digits>+<name>` for the public author's own handle only.
        handle = re.fullmatch(r"(?:\d+\+)?([a-z0-9-]+)", local)
        return local.endswith("[bot]") or bool(
            handle and author_handle and handle.group(1) == author_handle.lower()
        )
    return local in NOREPLY_LOCALS and any(under(domain, d) for d in VENDOR_DOMAINS)


def name_allowed(root: str, name: str) -> bool:
    name = name.lower()
    shared = name == SHARED_PROFILE and root.lower().endswith("users")
    return name in PLACEHOLDER_NAMES or shared


def leaks(text: str, author_email: str = "", author_handle: str = "") -> list[str]:
    found = [
        match.group(0)
        for match in POSIX_HOME.finditer(text)
        if not name_allowed(match.group(1), match.group(2))
    ]
    for pattern in (WINDOWS_HOME, WSL_SHARE_HOME):
        found.extend(
            match.group(0)
            for match in pattern.finditer(text)
            if not name_allowed("users", match.group(1))
        )
    found.extend(
        match.group(0)
        for match in EMAIL.finditer(text)
        if not email_allowed(match.group(1), match.group(2), author_email, author_handle)
    )
    return found


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=REPO, check=False, capture_output=True, text=True, encoding="utf-8"
    )
    return out.stdout.strip()


def at(name: str, domain: str) -> str:
    """An address built at run time, so this file does not trip its own scan."""
    return f"{name}@{domain}"


@pytest.mark.parametrize("root", ["/home", "/Users", "/mnt/c/Users", "/MNT/c/users"])
@pytest.mark.parametrize(
    "form",
    [
        "see {p}/alice/code",
        "HOME={p}/alice",
        "cd {p}/alice",
        'open "{p}/alice"',
        "PATH={p}/alice:/bin",
        "{p}/alice.",
    ],
)
def test_home_paths_with_a_real_name_are_leaks(root: str, form: str) -> None:
    found = leaks(form.format(p=root))
    assert len(found) == 1
    assert found[0].startswith(f"{root}/alice")


def test_windows_and_wsl_share_paths_with_a_real_name_are_leaks() -> None:
    drive, unc = "C:", "\\\\"
    for text in (
        f"{drive}\\Users\\alice\\x",
        f"{drive}\\\\Users\\\\alice",  # escaped in JSON or a string literal
        f"{drive.lower()}\\users\\alice",
        f"{unc}wsl${chr(92)}Ubuntu\\home\\alice\\x",
        f"{unc}wsl.localhost\\Ubuntu-24.04\\home\\alice",
    ):
        assert len(leaks(text)) == 1, text
        assert "alice" in leaks(text)[0], text


def test_placeholder_and_shared_profile_paths_are_fine() -> None:
    drive, unc = "C:", "\\\\"
    names = ("user", "runner", "<user>", "$USER")
    for root in ("/home", "/Users", "/mnt/c/Users"):
        assert leaks(" ".join(f"{root}/{name}/x" for name in names)) == []
    assert leaks("/mnt/c/Users/Public/Documents /Users/public") == []
    assert leaks(f"{drive}\\Users\\Public {drive}\\Users\\user") == []
    assert leaks(f"{unc}wsl$\\Ubuntu\\home\\user".replace("$", chr(36))) == []
    assert leaks("/home|/Users/ and /home/ alone and /mnt/c/Users") == []
    # `public` is only the shared profile under a Users folder.
    assert leaks("/home/" + "public/x") == ["/home/" + "public"]


def test_emails_outside_the_allow_list_are_leaks() -> None:
    for domain in ("gmail.com", "notanthropic.com", "anthropic.com.example.org", "x.testing"):
        address = at("jane", domain)
        assert leaks(f"mail {address} now") == [address]


@pytest.mark.parametrize(
    "address",
    [
        "a@example.com",
        "a@mail.example.com",
        "a@example.invalid",
        "a@host.test",
        "noreply@anthropic.com",
        "noreply@mail.anthropic.com",
        "no-reply@openai.com",
        "noreply@opencode.ai",
        "noreply@github.com",
        "copilot[bot]@users.noreply.github.com",
    ],
)
def test_placeholder_and_vendor_noreply_addresses_are_fine(address: str) -> None:
    assert leaks(f"<{address}>") == []


@pytest.mark.parametrize(
    ("local", "domain"),
    [
        ("codex", "openai.com"),
        ("bot", "anthropic.com"),
        ("jane", "opencode.ai"),
        ("jane", "github.com"),
        ("copilot", "users.noreply.github.com"),
        ("12345+alice", "users.noreply.github.com"),
    ],
)
def test_a_person_or_tool_address_at_a_vendor_is_a_leak(local: str, domain: str) -> None:
    address = at(local, domain)
    assert leaks(f"<{address}>") == [address]


def test_the_public_authors_noreply_address_is_allowed_for_their_handle_only() -> None:
    mine = at("12345+Alice", "users.noreply.github.com")
    theirs = at("12345+bob", "users.noreply.github.com")
    assert leaks(mine, author_handle="alice") == []
    assert leaks(theirs, author_handle="alice") == [theirs]
    assert leaks(mine) == [mine]


def test_the_public_author_address_is_allowed() -> None:
    address = at("me", "elsewhere.org")
    assert leaks(f"by {address}", author_email=address.title()) == []
    assert leaks(f"by {address}") == [address]


def test_tracked_files_hold_no_private_paths_or_addresses() -> None:
    # The repo-local public identity; both are empty in a bare CI clone.
    author, handle = git("config", "user.email"), git("config", "user.name")
    problems: list[str] = []
    for name in git("ls-files", "-z").split("\0"):
        if not name:
            continue
        data = (REPO / name).read_bytes()
        if b"\0" in data:  # a binary file
            continue
        for leak in leaks(data.decode("utf-8", errors="replace"), author, handle):
            problems.append(f"{name}: {leak}")
    assert problems == []
