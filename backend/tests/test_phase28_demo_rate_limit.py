"""SENTINEL — Phase 1I rate-limit diagnosis regression tests
(tests/test_phase28_demo_rate_limit.py)

Root cause of the demo 429s: all local traffic shares one 127.0.0.1 rate-limit
bucket, and the production default of 120 req/min is too tight for interactive
demo use (a single dashboard load is ~10 requests; repeated reloads and scenario
switches accumulate in the 60s window). The fix raises the default ONLY in demo
mode (SECURE_DEV_MODE=1) and ONLY when no explicit SENTINEL_RATE_LIMIT is set.

These tests pin that fix WITHOUT weakening production:
  * the production default is still exactly 120,
  * the demo default is strictly higher (so the demo cannot self-429),
  * an explicit SENTINEL_RATE_LIMIT always wins in BOTH modes (production can
    still be tightened; the demo can still be pinned),
  * turning demo mode OFF restores 120.

They set the two relevant vars explicitly via monkeypatch, so they are immune to
any local-.env contamination (monkeypatch writes os.environ directly).
"""

from __future__ import annotations

from app.security.config import SecurityConfig

PROD_DEFAULT = 120


def _clear(monkeypatch):
    monkeypatch.delenv("SENTINEL_RATE_LIMIT", raising=False)
    monkeypatch.delenv("SECURE_DEV_MODE", raising=False)


def test_production_default_rate_limit_is_unchanged(monkeypatch):
    _clear(monkeypatch)
    cfg = SecurityConfig.from_env()
    assert cfg.secure_dev_mode is False
    assert cfg.rate_limit_per_minute == PROD_DEFAULT


def test_demo_default_rate_limit_is_higher(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SECURE_DEV_MODE", "1")
    cfg = SecurityConfig.from_env()
    assert cfg.secure_dev_mode is True
    assert cfg.rate_limit_per_minute > PROD_DEFAULT


def test_explicit_limit_can_still_tighten_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SENTINEL_RATE_LIMIT", "50")
    cfg = SecurityConfig.from_env()
    # An explicit value is honoured verbatim — production is never silently widened.
    assert cfg.rate_limit_per_minute == 50


def test_explicit_limit_wins_in_demo(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SECURE_DEV_MODE", "1")
    monkeypatch.setenv("SENTINEL_RATE_LIMIT", "77")
    cfg = SecurityConfig.from_env()
    assert cfg.rate_limit_per_minute == 77


def test_disabling_demo_restores_production_default(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SECURE_DEV_MODE", "0")
    cfg = SecurityConfig.from_env()
    assert cfg.rate_limit_per_minute == PROD_DEFAULT


def test_blank_explicit_limit_falls_back_to_mode_default(monkeypatch):
    # A present-but-empty SENTINEL_RATE_LIMIT must not crash or read as 0.
    _clear(monkeypatch)
    monkeypatch.setenv("SENTINEL_RATE_LIMIT", "   ")
    assert SecurityConfig.from_env().rate_limit_per_minute == PROD_DEFAULT
    monkeypatch.setenv("SECURE_DEV_MODE", "1")
    assert SecurityConfig.from_env().rate_limit_per_minute > PROD_DEFAULT
