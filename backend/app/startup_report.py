"""SENTINEL — startup status report & banner (app/startup_report.py)

One place that answers "what is this process actually doing right now?", used by
three Phase-1 requirements at once:

  * 1B — the active authentication / demo mode is LOGGED at startup, so a demo is
    never silently mistaken for a hardened production deployment.
  * 1C — a runnable startup *validation* (``python -m app.startup_report``) reports
    backend bind, auth/demo mode, reconciliation status, LLM mode, and the
    physics / safety authority posture, with a non-zero exit for the
    mis-configured fail-closed state so it can gate a launch script.
  * 1K — the judge-facing startup banner.

It carries NO secret: the API key is reported only as "configured" / "not
configured". It imports no heavy runtime (no agent, no model) so it stays cheap
and safe to call at import/startup and from a test.

The physics / safety / LLM authority lines are architectural INVARIANTS, not
runtime-variable state: physics validation gates recovery
(``app/validation/physics.py``), safety is the final recovery authority
(``app/agent/safety.py``), and the LLM only ranks and explains — it can never
override a deterministic verdict. The demo path touches none of this, which is
exactly what ``tests/test_phase27_demo_mode.py`` asserts.
"""

from __future__ import annotations

import logging
import os

from app.security.config import SecurityConfig, security_mode

logger = logging.getLogger("sentinel.backend")

# Fixed authority spine — independent of security/demo mode.
PHYSICS_AUTHORITY = "AUTHORITY (deterministic; LLM cannot override)"
SAFETY_AUTHORITY = "AUTHORITY (final recovery gate; fail-closed)"
LLM_ROLE = "ASSISTIVE (ranks & explains; non-authoritative)"


def _llm_mode() -> str:
    return (os.environ.get("LLM_MODE", "").strip() or "cloud").lower()


def _backend_bind() -> str:
    host = os.environ.get("SENTINEL_HOST") or os.environ.get("HOST") or "127.0.0.1"
    port = os.environ.get("SENTINEL_PORT") or os.environ.get("PORT") or "8000"
    return f"http://{host}:{port}"


def build_startup_report(config: SecurityConfig | None = None) -> dict:
    """Assemble the secret-free status dict. Pure apart from reading env."""
    cfg = config or SecurityConfig.from_env()
    sec = security_mode(cfg)

    try:
        from app.reconciliation.config import reconciliation_enabled

        recon_enabled = reconciliation_enabled()
    except Exception:  # pragma: no cover - reconciliation is optional
        recon_enabled = False

    return {
        "backend": _backend_bind(),
        "security_mode": sec["mode"],
        "security_label": sec["label"],
        "auth_enforced": sec["auth_enforced"],
        "authenticated": sec["authenticated"],
        "api_key_configured": sec["api_key_configured"],  # boolean, never value
        "secure_dev_mode": sec["secure_dev_mode"],
        "reconciliation_enabled": recon_enabled,
        "llm_mode": _llm_mode(),
        "physics_authority": PHYSICS_AUTHORITY,
        "safety_authority": SAFETY_AUTHORITY,
        "llm_role": LLM_ROLE,
        "audit": "enabled (append-only, hash-sealed)",
        "warning": sec["warning"],
    }


def format_startup_banner(report: dict) -> str:
    """Render the report as the judge-facing banner (no secrets)."""
    bar = "═" * 66
    recon = "ENABLED" if report["reconciliation_enabled"] else "disabled (default)"
    key = "configured" if report["api_key_configured"] else "not configured"
    lines = [
        "",
        bar,
        "  SENTINEL — Spacecraft Diagnostic Copilot",
        bar,
        f"  Backend            : {report['backend']}",
        f"  Authentication     : {report['security_label']}",
        f"  Reconciliation     : {recon}",
        f"  LLM mode           : {report['llm_mode']}",
        f"  Physics authority  : {report['physics_authority']}",
        f"  Safety authority   : {report['safety_authority']}",
        f"  LLM role           : {report['llm_role']}",
        f"  Audit trail        : {report['audit']}",
        f"  API key            : {key}",
        bar,
    ]
    if report.get("warning"):
        lines.append(f"  !  {report['warning']}")
        lines.append(bar)
    lines.append("")
    return "\n".join(lines)


def log_startup_banner(config: SecurityConfig | None = None) -> dict:
    """Log the banner line-by-line (each line is short, so log truncation and
    redaction in production mode leave it intact) and return the report."""
    report = build_startup_report(config)
    for line in format_startup_banner(report).splitlines():
        logger.info(line)
    return report


if __name__ == "__main__":  # python -m app.startup_report [--json]
    import json as _json
    import sys

    rep = build_startup_report()
    if "--json" in sys.argv:
        print(_json.dumps(rep, indent=2))
    else:
        print(format_startup_banner(rep))
    # Exit non-zero ONLY for the mis-configured fail-closed posture, so this can
    # gate a launch script. Demo and production are both healthy (exit 0).
    sys.exit(2 if rep["security_mode"] == "FAIL_CLOSED_NO_KEY" else 0)
