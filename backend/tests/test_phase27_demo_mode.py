"""SENTINEL — Phase 1B authentication / demo-mode tests
(tests/test_phase27_demo_mode.py)

Pins the security posture the release depends on:

  1. Production with no credentials FAILS CLOSED (every request 401).
  2. Demo mode (SECURE_DEV_MODE=1, no key) serves without auth.
  3. Configured production rejects invalid / missing credentials.
  4. Demo mode does NOT alter the physics / safety authority spine.
  5. Demo mode does NOT silently enable reconciliation.
  6. The startup report / banner never exposes the API-key value.
  7. A demo process is never classified as production (and vice versa);
     the fail-closed mis-configuration is distinct and self-announcing.

These import only the security + startup modules (no agent), so they are fast,
deterministic, and free of the local-.env / socket-bind environment issues that
affect the heavier suites. Each test builds an explicit ``SecurityConfig`` or a
minimal app, so nothing depends on the ambient environment.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.config import (
    SECURITY_MODE_DEMO,
    SECURITY_MODE_FAIL_CLOSED,
    SECURITY_MODE_PRODUCTION,
    SecurityConfig,
    security_mode,
)
from app.security.middleware import SecurityMiddleware
from app.startup_report import build_startup_report, format_startup_banner


def _app_with(config: SecurityConfig) -> TestClient:
    """A one-route app guarded by SecurityMiddleware with the given config."""
    application = FastAPI()

    @application.get("/api/ping")
    def ping():
        return {"ok": True}

    application.add_middleware(SecurityMiddleware, config=config)
    return TestClient(application)


# ── 1. Production + no credentials → fail closed ─────────────────────────────
def test_production_no_credentials_fails_closed():
    # This is exactly what SecurityConfig.from_env() yields on a fresh clone
    # with no environment set (SECURE_DEV_MODE off, no SENTINEL_API_KEY).
    cfg = SecurityConfig(secure_dev_mode=False, auth_required=True, api_key=None)
    resp = _app_with(cfg).get("/api/ping")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHORIZED"


# ── 2. Demo mode serves without auth ─────────────────────────────────────────
def test_demo_mode_serves_without_auth():
    cfg = SecurityConfig(secure_dev_mode=True, auth_required=False, api_key=None)
    resp = _app_with(cfg).get("/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── 3. Configured production rejects invalid credentials ─────────────────────
def test_configured_production_rejects_invalid_credentials():
    cfg = SecurityConfig(secure_dev_mode=False, auth_required=True, api_key="RIGHT_KEY_1")
    client = _app_with(cfg)
    assert client.get("/api/ping").status_code == 401  # missing
    assert client.get("/api/ping", headers={"X-API-Key": "WRONG"}).status_code == 401
    assert client.get("/api/ping", headers={"X-API-Key": "RIGHT_KEY_1"}).status_code == 200


# ── 4. Demo mode does not alter the physics / safety authority spine ─────────
def test_demo_mode_preserves_physics_and_safety_authority():
    demo = build_startup_report(SecurityConfig(secure_dev_mode=True, auth_required=False))
    prod = build_startup_report(
        SecurityConfig(secure_dev_mode=False, auth_required=True, api_key="K")
    )
    for report in (demo, prod):
        assert "AUTHORITY" in report["physics_authority"]
        assert "AUTHORITY" in report["safety_authority"]
        assert "non-authoritative" in report["llm_role"].lower()
    # The authority lines are architectural invariants — identical in both modes.
    assert demo["physics_authority"] == prod["physics_authority"]
    assert demo["safety_authority"] == prod["safety_authority"]


# ── 5. Demo mode does not silently enable reconciliation ─────────────────────
def test_demo_mode_does_not_enable_reconciliation(monkeypatch):
    # Neutralise any local .env RECONCILIATION_ENABLED contamination explicitly.
    monkeypatch.setenv("RECONCILIATION_ENABLED", "")
    report = build_startup_report(SecurityConfig(secure_dev_mode=True, auth_required=False))
    assert report["reconciliation_enabled"] is False


# ── 6. Startup report / banner never exposes the API-key value ───────────────
def test_startup_report_never_exposes_api_key_value():
    secret = "SUPER_SECRET_KEY_VALUE_9c3f1a"
    cfg = SecurityConfig(secure_dev_mode=False, auth_required=True, api_key=secret)
    report = build_startup_report(cfg)
    banner = format_startup_banner(report)

    assert secret not in json.dumps(report)
    assert secret not in banner
    # The presence of a key is still reported — as a boolean / label only.
    assert report["api_key_configured"] is True
    assert "configured" in banner


# ── 7. Demo is never mistaken for production (and the modes are distinct) ─────
def test_modes_are_distinct_and_labelled():
    demo = security_mode(SecurityConfig(secure_dev_mode=True, auth_required=False))
    prod = security_mode(
        SecurityConfig(secure_dev_mode=False, auth_required=True, api_key="K")
    )
    fail_closed = security_mode(
        SecurityConfig(secure_dev_mode=False, auth_required=True, api_key=None)
    )

    assert demo["mode"] == SECURITY_MODE_DEMO
    assert demo["is_demo"] is True and demo["is_production"] is False
    assert demo["authenticated"] is False and demo["auth_enforced"] is False

    assert prod["mode"] == SECURITY_MODE_PRODUCTION
    assert prod["is_production"] is True and prod["is_demo"] is False
    assert prod["authenticated"] is True and prod["auth_enforced"] is True

    # Fail-closed is neither demo nor production: auth enforced, but nothing can
    # authenticate, so every request is rejected — and it warns about itself.
    assert fail_closed["mode"] == SECURITY_MODE_FAIL_CLOSED
    assert fail_closed["auth_enforced"] is True
    assert fail_closed["authenticated"] is False
    assert "SECURE_DEV_MODE" in (fail_closed["warning"] or "")


# ── Extra: the banner reports every status 1C/1K require ─────────────────────
def test_banner_reports_all_required_statuses():
    banner = format_startup_banner(
        build_startup_report(SecurityConfig(secure_dev_mode=True, auth_required=False))
    )
    for label in (
        "Backend",
        "Authentication",
        "Reconciliation",
        "LLM mode",
        "Physics authority",
        "Safety authority",
        "Audit trail",
    ):
        assert label in banner, f"startup banner missing '{label}' line"
