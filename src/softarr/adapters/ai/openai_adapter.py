"""OpenAI-compatible AI adapter.

Sends requests to any OpenAI-compatible chat completion endpoint. Works with:
  - OpenAI (api.openai.com)
  - Azure OpenAI
  - Local Ollama (http://localhost:11434/v1)
  - xAI Grok (if using OpenAI-compatible proxy)
  - Any other OpenAI-compatible provider

Configuration (via softarr.ini [ai] section):
  base_url   -- API base URL (default: https://api.openai.com/v1)
  api_key    -- Provider API key
  model      -- Model identifier (default: gpt-4o-mini)
"""

import logging

import httpx

from softarr.adapters.ai.base import AIAdapter

logger = logging.getLogger("softarr.ai.openai")

REQUEST_TIMEOUT = 30.0
MAX_TOKENS = 1024


class OpenAIAdapter(AIAdapter):
    """OpenAI-compatible chat completion adapter."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def ask(self, prompt: str, system: str) -> str:
        """Send a chat completion request.

        Returns the assistant's message content.
        Raises ``RuntimeError`` on API or network errors.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.3,  # Low temperature for factual responses
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            raise RuntimeError("AI provider request timed out")
        except httpx.RequestError as exc:
            raise RuntimeError(f"AI provider unreachable: {exc}") from exc

        if resp.status_code == 401:
            raise RuntimeError("AI provider returned 401 -- check API key")
        if resp.status_code == 429:
            raise RuntimeError("AI provider rate limit exceeded")
        if resp.status_code != 200:
            raise RuntimeError(
                f"AI provider returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(
                f"Unexpected AI provider response format: {exc}"
            ) from exc
