"""AI assistant service.

Orchestrates AI-assisted queries for the three allowed scenarios:
  - discovery:   "Find alternatives to X"
  - risk:        "Is this release safe?"
  - comparison:  "Which version should I use?"

The service enforces the strict system prompt specified in the requirements,
applies an in-memory token-bucket rate limiter per session/user, and delegates
actual API calls to the configured AIAdapter.

Configuration (softarr.ini [ai] section):
  enabled               -- master toggle
  provider              -- "openai" (only supported value currently)
  api_key               -- provider API key
  base_url              -- provider base URL (OpenAI-compatible)
  model                 -- model identifier
  rate_limit_per_hour   -- maximum requests per hour (per process, not per user)
"""

import logging
import time
from threading import Lock
from typing import Optional

from softarr.core.ini_settings import IniSettingsManager

logger = logging.getLogger("softarr.ai_service")

# ---------------------------------------------------------------------------
# Strict system prompt -- must not be modified
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are an assistant embedded inside a self-hosted software download platform.

You must:
- Provide concise, factual answers
- Focus on software, safety, and alternatives
- Avoid speculation
- Avoid legal advice
- Prefer open-source or legitimate software options when possible

You must NOT:
- Encourage piracy
- Recommend unsafe or unverified downloads
- Provide instructions to bypass licensing or protections

When suggesting software:
- Include 2 to 5 alternatives
- Prefer well-known, trusted tools
- Briefly explain each option

When assessing risk:
- Explain why something may be unsafe
- Refer to metadata such as source, naming, and trust signals

Keep responses short and structured."""

# Valid user-facing scenarios
VALID_SCENARIOS = {"discovery", "risk", "comparison"}

# ---------------------------------------------------------------------------
# In-memory token bucket rate limiter (process-wide, not per-user)
# ---------------------------------------------------------------------------
_rate_lock = Lock()
_rate_tokens: float = 0.0
_rate_last_refill: float = 0.0
_rate_capacity: float = 0.0


def _init_rate_limiter(capacity: int) -> None:
    global _rate_tokens, _rate_last_refill, _rate_capacity
    with _rate_lock:
        _rate_capacity = float(capacity)
        _rate_tokens = float(capacity)
        _rate_last_refill = time.monotonic()


def _consume_token() -> bool:
    """Consume one token. Returns True if allowed, False if rate-limited."""
    global _rate_tokens, _rate_last_refill
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _rate_last_refill
        # Refill at capacity/3600 tokens per second (hourly bucket)
        refill = elapsed * (_rate_capacity / 3600.0)
        _rate_tokens = min(_rate_capacity, _rate_tokens + refill)
        _rate_last_refill = now
        if _rate_tokens >= 1.0:
            _rate_tokens -= 1.0
            return True
        return False


class AIService:
    """Orchestrates AI assistant queries with rate limiting and prompt enforcement."""

    def __init__(self, ini: IniSettingsManager) -> None:
        self.ini = ini

    def is_enabled(self) -> bool:
        return (self.ini.get("ai_enabled") or "false").lower() == "true"

    def _build_adapter(self):
        from softarr.adapters.ai.openai_adapter import OpenAIAdapter

        base_url = self.ini.get("ai_base_url") or "https://api.openai.com/v1"
        api_key = self.ini.get("ai_api_key") or ""
        model = self.ini.get("ai_model") or "gpt-4o-mini"
        return OpenAIAdapter(base_url=base_url, api_key=api_key, model=model)

    async def ask(
        self,
        scenario: str,
        context: str,
        user: Optional[str] = None,
    ) -> str:
        """Handle an AI query for the given scenario.

        ``scenario`` must be one of: discovery, risk, comparison.
        ``context`` is the user's freeform question or software name.

        Returns the assistant's text response.
        Raises ``ValueError`` for invalid input.
        Raises ``PermissionError`` if rate limit is exceeded.
        Raises ``RuntimeError`` if AI is disabled or the API call fails.
        """
        if not self.is_enabled():
            raise RuntimeError("AI assistant is not enabled")

        scenario = (scenario or "").lower().strip()
        if scenario not in VALID_SCENARIOS:
            raise ValueError(
                f"Invalid scenario '{scenario}'. Valid values: {', '.join(sorted(VALID_SCENARIOS))}"
            )

        context = (context or "").strip()
        if not context:
            raise ValueError("Context must not be empty")
        if len(context) > 2000:
            raise ValueError("Context exceeds maximum length of 2000 characters")

        # Rate limiting
        rate_limit = int(self.ini.get("ai_rate_limit_per_hour") or 20)
        global _rate_capacity
        if _rate_capacity != float(rate_limit):
            _init_rate_limiter(rate_limit)
        if not _consume_token():
            raise PermissionError("AI rate limit exceeded -- try again later")

        # Build scenario-specific prompt
        prompt = self._build_prompt(scenario, context)
        adapter = self._build_adapter()

        logger.info(
            "AI query: scenario=%s user=%s context_len=%d",
            scenario,
            user or "anonymous",
            len(context),
        )

        return await adapter.ask(prompt, _SYSTEM_PROMPT)

    @staticmethod
    def _build_prompt(scenario: str, context: str) -> str:
        """Build a focused prompt for the given scenario."""
        if scenario == "discovery":
            return (
                f"Find alternatives to the following software: {context}\n\n"
                "List 2 to 5 well-known, trusted alternatives with a brief explanation of each."
            )
        if scenario == "risk":
            return (
                f"Assess the safety of this software release: {context}\n\n"
                "Explain any risk factors based on naming conventions, source indicators, "
                "or other trust signals. Be concise."
            )
        if scenario == "comparison":
            return (
                f"Help me choose between these software options: {context}\n\n"
                "Compare them briefly and recommend the most suitable choice for a typical user."
            )
        return context
