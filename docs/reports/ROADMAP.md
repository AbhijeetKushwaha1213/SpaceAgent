# ROADMAP.md — SENTINEL Next Steps

**Derived from:** `PROJECT_STATUS.md` audit (2026-08-19, score 72/100)  
**Goal:** Late Alpha → Beta → Production-Ready  

---

## Phase 1 — Immediate Fixes (Target: this week)

> Quick wins that raise the score from 72 → ~80 with minimal risk.

- [x] **Fix test collection** — Remove `sys.exit(0)` from 7 test files so `pytest` can collect them
  - `tests/test_agent.py`
  - `tests/test_models.py`
  - `tests/test_rag.py`
  - `tests/test_safety.py`
  - `tests/test_pipeline.py`
  - `tests/test_streaming.py`
  - `tests/test_prompts.py`
  - Guard the `sys.exit()` behind `if __name__ == "__main__"` only
  - Effort: ~1 hour

- [x] **Update 15 failing frontend contract tests** — Sync `test_phase3_contract.py` and `test_phase0_frontend.py` to match the current `SentinelProvider` + tabbed-view `App.jsx`
  - Effort: 2–4 hours

- [x] **Delete `simulation/simulator.py`** — 16-line hackathon artifact; `fault_simulator.py` supersedes it entirely
  - Effort: 5 minutes

- [x] **Add `requirements-dev.txt`** — Separate prod from dev dependencies (`pytest`, `coverage`, `ruff`/`flake8`)
  - Effort: 30 minutes

- [x] **Fix Pydantic deprecation warning** — `CrashDumpRequest` uses class-based `Config`; migrate to `ConfigDict`
  - `app/api/models.py:755`
  - Effort: 15 minutes

---

## Phase 2 — Beta Release Blockers (Target: next 1–2 weeks)

> Without these, the system cannot be handed to anyone outside the dev team.

- [x] **Enforce API key authentication by default**
  - Make `SENTINEL_API_KEY` required (not optional) when not in `SECURE_DEV_MODE`
  - Return `401 Unauthorized` if missing
  - `app/security/config.py` + `app/security/middleware.py`
  - Effort: 2 hours

- [x] **Dockerfile + docker-compose.yml**
  - Multi-stage build (deps → app)
  - Include ChromaDB volume mount for persistent vector store
  - Compose: backend + frontend + optional Ollama sidecar for LOCAL mode
  - Effort: 4–8 hours

- [x] **CI/CD pipeline** (GitHub Actions)
  - Lint (`ruff`) → Unit tests (`pytest`) → Contract tests → Build Docker image
  - Block merge on test failure
  - Effort: 1 day

- [x] **End-to-end integration tests**
  - Spin up the FastAPI server in-process (`TestClient`)
  - POST a crash dump → assert SSE event stream → validate `SentinelOutput`
  - Cover: happy path, invalid input, rate limit, auth rejection
  - Effort: 1–2 days

- [x] **Add `conftest.py`** with shared fixtures
  - Stub agent, mock LLM provider, sample crash dumps, temp audit store
  - Effort: 2–4 hours

---

## Phase 3 — Production Hardening (Target: next 2–4 weeks)

> Ops-layer work that makes the system deployable and monitorable.

- [ ] **Per-client rate limiting** — Replace global counter with per-IP or per-API-key tracking
  - Effort: 2–4 hours

- [ ] **Structured logging with correlation IDs**
  - Assign a `request_id` at ingestion; propagate through every pipeline stage
  - Use structured JSON logging (e.g., `structlog`)
  - Effort: 1–2 days

- [ ] **Metrics export** — Prometheus `/metrics` endpoint or OpenTelemetry traces
  - Pipeline latency per stage, LLM call duration, safety block rate, RAG hit rate
  - Effort: 1 day

- [ ] **Load / stress testing** — Locust or k6 harness
  - Target: 50 concurrent crash dump analyses without degradation
  - Effort: 1–2 days

- [ ] **Kubernetes / Helm manifests**
  - Deployment, Service, ConfigMap, Secrets, HPA
  - Effort: 1–2 days

- [ ] **Operator runbook / documentation**
  - Deployment guide, environment variable reference, troubleshooting, architecture diagram
  - Effort: 2–3 days

- [ ] **CORS hardening** — Lock down to actual deployment origins (not just localhost)
  - Effort: 30 minutes

---

## Phase 4 — Feature Roadmap (v2)

> New capabilities beyond hardening the existing pipeline.

- [ ] **Real-time telemetry ingestion adapter** — WebSocket or gRPC stream ingestion replacing snapshot-only analysis
  - Effort: 1–2 weeks

- [ ] **LangGraph tool-node integration** — Implement `query_telemetry()` and `propose_recovery()` (currently commented stubs at `agent.py:2013-2036`)
  - Effort: 1–2 weeks

- [ ] **Fine-tuned model integration** — `tuned_model_id` support exists in config but no tuned model is available yet
  - Effort: Depends on training pipeline

- [ ] **Multi-spacecraft routing** — Support multiple vehicle IDs in a single deployment
  - Effort: 1 week

- [ ] **RBAC** — Role-based access control (Operator vs. Engineer vs. Auditor)
  - Effort: 1 week

- [ ] **Expanded fault library** — Add fault types beyond the current 6 (payload faults, propulsion, GNC modes)
  - Effort: 1–2 weeks per fault class

---

## Progress Tracking

| Phase | Items | Done | % |
|-------|-------|------|---|
| 1 — Immediate Fixes | 5 | 5 | 100% |
| 2 — Beta Blockers | 5 | 5 | 100% |
| 3 — Production Hardening | 7 | 0 | 0% |
| 4 — Feature Roadmap | 6 | 0 | 0% |

---

*Last updated: 2026-08-19 (Phase 1 + Phase 2 complete)*
