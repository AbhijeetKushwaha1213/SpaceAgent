"""SENTINEL Security Middleware (app/security/middleware.py)

Implements:
  1. X-Correlation-ID request tracking & response header injection.
  2. Request size limiter (HTTP 413).
  3. Sliding window IP rate limiter (HTTP 429).
  4. API authentication check (HTTP 401).
  5. Generic 500 exception handler (no stack trace exposure).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.security.auth import verify_api_key
from app.security.config import SecurityConfig

logger = logging.getLogger("sentinel.security")


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter per client IP."""

    def __init__(self, limit_per_minute: int = 120):
        self.limit = limit_per_minute
        self.requests: dict[str, list[float]] = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        window_start = now - 60.0

        timestamps = self.requests.get(client_ip, [])
        # Evict timestamps older than 60s
        valid_timestamps = [t for t in timestamps if t > window_start]

        if len(valid_timestamps) >= self.limit:
            self.requests[client_ip] = valid_timestamps
            return False

        valid_timestamps.append(now)
        self.requests[client_ip] = valid_timestamps
        return True


_RATE_LIMITER = SlidingWindowRateLimiter(limit_per_minute=120)


class SecurityMiddleware(BaseHTTPMiddleware):
    """FastAPI Middleware for correlation IDs, size limits, rate limits, and auth."""

    def __init__(self, app: Callable, config: SecurityConfig | None = None):
        super().__init__(app)
        self.config = config or SecurityConfig.from_env()
        self.rate_limiter = SlidingWindowRateLimiter(self.config.rate_limit_per_minute)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 1. Correlation ID
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # 2. Authentication Check
        if self.config.api_key:
            auth_header = request.headers.get("Authorization") or request.headers.get("X-API-Key")
            if not verify_api_key(auth_header, self.config):
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Unauthorized API key",
                        "error_code": "UNAUTHORIZED",
                        "correlation_id": correlation_id,
                    },
                    headers={"X-Correlation-ID": correlation_id},
                )

        # 3. Request Size Limit Check
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length_bytes = int(content_length)
                if length_bytes > self.config.max_payload_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request payload exceeds max size limit",
                            "error_code": "PAYLOAD_TOO_LARGE",
                            "correlation_id": correlation_id,
                        },
                        headers={"X-Correlation-ID": correlation_id},
                    )
            except ValueError:
                pass

        # 4. Rate Limiting Check
        client_ip = request.client.host if request.client else "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before retrying.",
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "correlation_id": correlation_id,
                },
                headers={"X-Correlation-ID": correlation_id},
            )

        # 5. Execute Request Handler & Catch Unhandled Exceptions
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        except Exception as exc:
            logger.error(
                "Internal server error [Correlation-ID: %s]: %s",
                correlation_id, exc, exc_info=False,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "error_code": "INTERNAL_ERROR",
                    "correlation_id": correlation_id,
                },
                headers={"X-Correlation-ID": correlation_id},
            )
