# SENTINEL backend — multi-stage build (deps → app).
#
# Stage 1 installs production dependencies into a separate prefix so the
# runtime image stays small and layers stay cacheable.

# ---------- Stage 1: dependencies ----------
FROM python:3.12-slim AS deps

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS app

WORKDIR /app

COPY --from=deps /install /usr/local
COPY . /app

EXPOSE 8000

# Production default: API key authentication is enforced (401 without
# SENTINEL_API_KEY). Set SECURE_DEV_MODE=1 only for local development.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
