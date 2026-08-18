"""
SENTINEL — LLM Provider Abstraction (llm/provider.py)

Phase 10.  Pluggable LLM backend supporting Gemini, local models, and stubs.

Every provider implements ``call(messages) → str``.  The provider is selected
by ``AgentConfig.mode`` (reusing the existing ``ModelMode`` enum from
``agent.py``).

The actual client creation logic that was inline in ``SentinelAgent._call_gemini()``
and ``_call_fallback()`` is extracted here so the constrained ranking pipeline
can call an LLM without depending on the full SentinelAgent class.

Architecture:
    LLMProvider (ABC)
    ├── GeminiProvider      Gemini via google-genai
    ├── LocalProvider       Ollama / vLLM via OpenAI-compat API
    └── StubProvider        Deterministic test stub (no inference)
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("sentinel.llm.provider")


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER CONFIG
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderConfig:
    """Provider-agnostic LLM configuration.

    Phase 11: Decoupled configuration with environment variable defaults
    (LLM_MODE, LLM_BASE_URL, LLM_MODEL, GEMINI_API_KEY).
    """
    # Gemini / Cloud
    model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gemini-2.5-flash"))
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    tuned_model_id: str = ""

    # Local / Sovereign (OpenAI-compatible)
    fallback_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "phi-3-mini"))
    fallback_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"))
    fallback_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", "local"))

    # Stub
    stub_response: str = ""
    stub_label: str = ""

    # Shared
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout_seconds: float = 90.0

    def resolved_gemini_key(self) -> str:
        """Resolve API key from config or environment."""
        key = self.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ProviderError(
                "No Gemini API key found. Set GEMINI_API_KEY in .env or "
                "pass gemini_api_key to ProviderConfig."
            )
        return key

    @property
    def active_model_name(self) -> str:
        """Return the model name currently configured."""
        if self.tuned_model_id:
            return self.tuned_model_id
        return self.model


# ═══════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════

class ProviderError(Exception):
    """Raised when an LLM provider call fails."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# ABSTRACT BASE
# ═══════════════════════════════════════════════════════════════════════════

class LLMProvider(ABC):
    """Abstract LLM provider.

    Every provider implements ``call(messages) → str`` and ``provider_name``.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name for audit logs."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier for audit logs."""
        ...

    @abstractmethod
    def call(self, messages: list[dict[str, str]]) -> str:
        """Send a chat-completion request and return the raw text response.

        Args:
            messages: Standard chat-completion format
                ``[{"role": "system", "content": ...}, ...]``

        Returns:
            Raw text content of the assistant's response.

        Raises:
            ProviderError: On any API or network failure.
        """
        ...

    @property
    def inference_performed(self) -> bool:
        """Whether this provider actually runs inference (vs. stub)."""
        return True


# ═══════════════════════════════════════════════════════════════════════════
# GEMINI PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class GeminiProvider(LLMProvider):
    """Gemini via google-genai SDK.

    Supports base Gemini Flash and tuned model endpoints.
    Forces JSON output mode when available.
    """

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self.config.active_model_name

    @property
    def _gemini_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise ProviderError(
                    "google-genai package not installed. "
                    "Run: pip install google-genai"
                )
            self._client = genai.Client(
                api_key=self.config.resolved_gemini_key(),
            )
        return self._client

    def call(self, messages: list[dict[str, str]]) -> str:
        try:
            from google.genai import types

            model_id = self.config.active_model_name

            # Split messages into system instruction + contents
            system_text = None
            contents: list[str] = []
            for msg in messages:
                if msg["role"] == "system":
                    system_text = msg["content"]
                else:
                    contents.append(msg["content"])

            # Disable thinking scratchpad for 2.5 models
            thinking_config = None
            if "2.5" in model_id:
                try:
                    thinking_config = types.ThinkingConfig(thinking_budget=0)
                except Exception:
                    pass

            gen_config = types.GenerateContentConfig(
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_tokens,
                system_instruction=system_text,
                response_mime_type="application/json",
                **({
                    "thinking_config": thinking_config
                } if thinking_config is not None else {}),
            )

            response = self._gemini_client.models.generate_content(
                model=model_id,
                contents=contents,
                config=gen_config,
            )

            content = response.text
            if not content:
                raise ProviderError("Gemini returned empty response content")

            logger.info(
                "Gemini response (first 300 chars): %s",
                content[:300].replace("\n", " "),
            )
            return content

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                f"Gemini API call failed ({type(e).__name__}): {e}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# LOCAL / FALLBACK PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

import json
import urllib.request
import urllib.error


class LocalProvider(LLMProvider):
    """Local / sovereign open model via OpenAI-compatible API.

    Works with any server exposing /v1/chat/completions (vLLM, LM Studio, Ollama,
    LocalAI, Jan, etc.). Does not hardcode vendor-specific software.
    """

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def model_name(self) -> str:
        return self.config.fallback_model or "local-model"

    def call(self, messages: list[dict[str, str]]) -> str:
        base_url = self.config.fallback_base_url.rstrip("/")
        if not base_url.endswith("/chat/completions"):
            endpoint = f"{base_url}/chat/completions"
        else:
            endpoint = base_url

        # Attempt call via openai package first if installed
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=self.config.fallback_base_url,
                api_key=self.config.fallback_api_key or "local",
                timeout=self.config.timeout_seconds,
            )
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            content = response.choices[0].message.content
            if not content:
                raise ProviderError("Local LLM returned empty response content")
            return content
        except ImportError:
            # Zero-dependency HTTP fallback using urllib
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.fallback_api_key or 'local'}",
            }
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content = resp_data["choices"][0]["message"]["content"]
                    if not content:
                        raise ProviderError("Local LLM returned empty response content")
                    return content
            except Exception as e:
                raise ProviderError(f"Local LLM call failed ({type(e).__name__}): {e}")
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Local LLM call failed ({type(e).__name__}): {e}")


# ═══════════════════════════════════════════════════════════════════════════
# STUB PROVIDER — deterministic test stub
# ═══════════════════════════════════════════════════════════════════════════

class StubProvider(LLMProvider):
    """No inference performed. Returns a pre-configured response.

    Used for tests and the worked example so the full pipeline can be
    exercised without an API key. The audit trail records
    ``provider="stub"`` and ``inference_performed=False``.
    """

    def __init__(
        self,
        response: str = "",
        label: str = "stub",
    ):
        self._response = response
        self._label = label

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return f"stub:{self._label}"

    @property
    def inference_performed(self) -> bool:
        return False

    def call(self, messages: list[dict[str, str]]) -> str:
        if not self._response:
            raise ProviderError(
                "StubProvider requires a pre-configured response; "
                "refusing to invent one"
            )
        logger.info(
            "LLM call served from stub '%s' — no inference performed.",
            self._label,
        )
        return self._response


# ═══════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_provider(
    mode: str = "cloud",
    config: ProviderConfig | None = None,
    stub_response: str = "",
    stub_label: str = "inline",
) -> LLMProvider:
    """Create an LLM provider from a mode string.

    Args:
        mode: One of "cloud", "local", "base", "tuned", "fallback", "stub".
        config: Optional provider configuration.
        stub_response: Response for stub mode.
        stub_label: Label for stub mode.

    Returns:
        An LLMProvider instance.

    Raises:
        ValueError: If mode is unrecognised.
    """
    mode_lower = (mode or "").lower().strip()

    if mode_lower == "stub":
        return StubProvider(
            response=stub_response or (config.stub_response if config else ""),
            label=stub_label or (config.stub_label if config else "inline"),
        )
    elif mode_lower in ("cloud", "base", "tuned"):
        return GeminiProvider(config=config)
    elif mode_lower in ("local", "fallback"):
        return LocalProvider(config=config)
    else:
        raise ValueError(
            f"Unknown LLM provider mode: '{mode}'. "
            f"Expected one of: cloud, local, base, tuned, fallback, stub"
        )
