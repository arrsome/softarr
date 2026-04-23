"""Abstract base class for AI provider adapters."""

from abc import ABC, abstractmethod


class AIAdapter(ABC):
    """Common interface for AI provider adapters.

    Concrete implementations send the prompt to the configured provider and
    return the assistant's text response. All adapters must honour the
    system prompt supplied by the caller -- it must not be overridden.
    """

    @abstractmethod
    async def ask(self, prompt: str, system: str) -> str:
        """Send a user prompt with the given system instruction.

        Returns the assistant's plain-text response.
        Raises ``RuntimeError`` on API errors.
        """
