"""OpenHands backend for Kato.

Public surface:
    KatoClient - HTTP client that drives an OpenHands server. Implements
                 the :class:`kato.client.agent_client.AgentClient` contract.

The OpenHands client leans on ``kato.client.openrouter.OpenRouterClient``
when the configured LLM base URL points at OpenRouter, but that helper
lives in its own ``openrouter/`` package now (provider-specific helpers
don't belong under another provider's namespace).
"""

from kato.client.openhands.openhands_client import KatoClient  # noqa: F401
