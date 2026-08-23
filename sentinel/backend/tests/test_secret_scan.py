"""
SENTINEL — Secret-scan regression guard (tests/test_secret_scan.py)

Production-hardening (Phase 1A). A real Google/Gemini API key was once committed
to ``sentinel/.env.example`` and later removed from the working tree. The value
still survives in remote git history and MUST be rotated — the incident, the
exposure and the (human-performed) history remediation are documented in the
repository-root ``SECURITY.md``.

This test does NOT scan git history (that is a one-time, human-approved
remediation, not something a unit test should attempt). Its job is narrower and
permanent: keep the *working tree* free of live-looking credentials from here on,
so a key can never SILENTLY re-enter tracked source.

It is deterministic and offline — it shells out to ``git`` to enumerate tracked
files and reads them from disk. No network, no import of application code, so it
behaves identically on a laptop and in CI.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

# ── What a Google API key looks like ─────────────────────────────────────────
# "AIzaSy" + 33 url-safe chars = 39 chars total. We match 30+ trailing chars so
# the pattern also catches over-length synthetic fixtures, then rely on the
# allowlist (below) to exempt the files that intentionally hold such fixtures.
_GOOGLE_KEY = re.compile(r"AIzaSy[0-9A-Za-z_\-]{30,}")

# Files that legitimately contain SYNTHETIC key-shaped strings: the redaction /
# exfiltration-defence tests assert that such strings get masked before any
# external call, and the redaction source documents the prefix it strips. These
# are fixtures and documentation, never real credentials. Paths are
# repository-root-relative, exactly as ``git ls-files`` emits them.
_ALLOWLIST = frozenset(
    {
        "sentinel/backend/tests/test_phase14_security.py",
        "sentinel/backend/tests/test_phase23_cloud_redaction.py",
        "sentinel/backend/tests/test_phase25_adversarial_security.py",
        "sentinel/backend/app/security/redaction.py",
        "sentinel/backend/tests/test_secret_scan.py",  # this guard itself
    }
)

# Environment variables whose *value* is a secret. A ``*.env.example`` template
# may name these, but only with a blank or obviously-placeholder value.
_SECRET_VAR = re.compile(
    r"^\s*([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)\s*=\s*(.*)$"
)
_PLACEHOLDER_HINTS = (
    "your-", "your_", "placeholder", "example", "changeme", "change-me",
    "change_me", "xxxx", "<", "replace", "todo", "dummy", "none", "here",
)


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if out.returncode != 0:
        pytest.skip("not a git repository — secret scan needs the tracked file list")
    return Path(out.stdout.strip())


def _tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return [p for p in out.stdout.split("\0") if p]


def _read_text(root: Path, rel: str) -> str | None:
    """Return decoded text, or None for unreadable / binary files."""
    try:
        data = (root / rel).read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:  # crude binary sniff — skip binaries
        return None
    return data.decode("utf-8", errors="ignore")


def test_no_google_api_key_shaped_string_in_tracked_tree():
    """No live-looking Google/Gemini key may appear in any tracked file except
    the documented redaction-fixture allowlist."""
    root = _repo_root()
    offenders: list[str] = []
    for rel in _tracked_files(root):
        if rel in _ALLOWLIST:
            continue
        text = _read_text(root, rel)
        if text is None:
            continue
        if _GOOGLE_KEY.search(text):
            offenders.append(rel)
    assert not offenders, (
        "Possible live Google/Gemini API key committed in: "
        + ", ".join(sorted(offenders))
        + " — rotate it immediately (see SECURITY.md) and remove it from the "
        "working tree. If this is an intentional synthetic fixture, add the path "
        "to _ALLOWLIST in tests/test_secret_scan.py with a justifying comment."
    )


def test_no_real_dotenv_file_is_tracked():
    """Only ``*.env.example`` templates may be tracked; a real ``.env`` /
    ``.env.local`` / ``prod.env`` must never be committed."""
    root = _repo_root()
    bad: list[str] = []
    for rel in _tracked_files(root):
        name = os.path.basename(rel)
        looks_like_env = (
            name == ".env"
            or name.startswith(".env.")   # .env.local, .env.production, ...
            or name.endswith(".env")      # prod.env, staging.env, ...
        )
        if looks_like_env and not name.endswith(".example"):
            bad.append(rel)
    assert not bad, (
        "Real dotenv file(s) are tracked (only *.env.example may be committed): "
        + ", ".join(sorted(bad))
    )


def test_env_example_secret_values_are_placeholders_only():
    """A tracked ``*.env.example`` may name secret variables, but every such
    value must be blank or an obvious placeholder — never a real-looking secret."""
    root = _repo_root()
    offenders: list[str] = []
    for rel in _tracked_files(root):
        if not rel.endswith(".env.example"):
            continue
        text = _read_text(root, rel) or ""
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue  # commented lines are documentation, not live values
            m = _SECRET_VAR.match(line)
            if not m:
                continue
            name = m.group(1)
            value = m.group(2).strip().strip('"').strip("'").strip()
            if value == "":
                continue  # blank is the safest placeholder
            low = value.lower()
            if any(hint in low for hint in _PLACEHOLDER_HINTS):
                continue  # obvious placeholder text
            # Anything left that is key-shaped, or a long mixed alphanumeric
            # token, is treated as a real secret that must not be committed.
            looks_secret = bool(_GOOGLE_KEY.search(value)) or (
                len(value) >= 20
                and re.search(r"\d", value)
                and re.search(r"[A-Za-z]", value)
            )
            if looks_secret:
                offenders.append(f"{rel}:{name}")
    assert not offenders, (
        "*.env.example contains non-placeholder secret value(s): "
        + ", ".join(sorted(offenders))
        + " — replace with a placeholder such as 'your-api-key-here'."
    )
