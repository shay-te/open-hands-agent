"""OpenRouter client package.

Provider-specific helpers for routing model calls through OpenRouter.
Lives here (not under ``openhands/``) so any backend that wants to
validate OpenRouter-hosted models — OpenHands today, others later —
imports from one canonical location.
"""

from kato.client.openrouter.openrouter_client import OpenRouterClient  # noqa: F401
