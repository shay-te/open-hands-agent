"""Build the configured agent backend.

Takes config + an ``AgentPlatform`` selector, returns something that
satisfies ``agent_provider_contracts.AgentProvider``.

The factory keeps the runtime knobs (``docker_mode_on``,
``read_only_tools_on``, ``testing``) explicit rather than burying
them in config because each one materially changes how the
backend behaves at spawn time and operators have been bitten by
silent config inheritance there before.
"""

from __future__ import annotations

from typing import Any

from agent_provider_contracts.agent_provider_contracts.agent_provider import (
    AgentProvider,
)

from agent_core_lib.agent_core_lib.platform import AgentPlatform


# Aliases the operator can write in the agent_backend config field that
# resolve to canonical platforms. Centralised here so a future
# alias rename lands in one place rather than scattered string
# checks across the codebase.
_PLATFORM_ALIASES: dict[str, AgentPlatform] = {
    'claude': AgentPlatform.CLAUDE,
    'claude-code': AgentPlatform.CLAUDE,
    'claude_code': AgentPlatform.CLAUDE,
    'claude-cli': AgentPlatform.CLAUDE,
    'claude_cli': AgentPlatform.CLAUDE,
    'codex': AgentPlatform.CODEX,
    'codex-cli': AgentPlatform.CODEX,
    'codex_cli': AgentPlatform.CODEX,
    'openai-codex': AgentPlatform.CODEX,
    'openai_codex': AgentPlatform.CODEX,
    'openhands': AgentPlatform.OPENHANDS,
    'open-hands': AgentPlatform.OPENHANDS,
    'open_hands': AgentPlatform.OPENHANDS,
    '': AgentPlatform.OPENHANDS,  # historical default
}


def resolve_platform(name: str) -> AgentPlatform:
    """Map an operator-supplied agent backend name to an ``AgentPlatform`` enum."""
    key = (name or '').strip().lower()
    if key not in _PLATFORM_ALIASES:
        raise ValueError(
            f'unsupported agent backend: {name!r}; '
            f'supported values: {sorted({p.value for p in AgentPlatform})}'
        )
    return _PLATFORM_ALIASES[key]


class AgentClientFactory(object):
    """Construct the configured ``AgentProvider`` implementation.

    Lazy on imports — the Claude impl pulls in the streaming
    session machinery, the OpenHands impl pulls in the HTTP
    client. Importing only the one we need keeps the boot of an
    OpenHands-only install free of Claude's transitive
    dependencies (and vice versa).
    """

    def __init__(
        self,
        *,
        max_retries: int,
        testing: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
    ) -> None:
        self._max_retries = max_retries
        self._testing = testing
        self._docker_mode_on = docker_mode_on
        self._read_only_tools_on = read_only_tools_on

    def build(self, platform: AgentPlatform, cfg: Any) -> AgentProvider:
        if platform == AgentPlatform.CLAUDE:
            return self._build_claude(cfg)
        if platform == AgentPlatform.CODEX:
            return self._build_codex(cfg)
        if platform == AgentPlatform.OPENHANDS:
            return self._build_openhands(cfg)
        raise ValueError(f'unhandled agent platform: {platform!r}')

    def _build_claude(self, open_cfg: Any) -> AgentProvider:
        # Imported lazily so an OpenHands-only install never
        # touches the Claude streaming machinery.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        claude_cfg = getattr(open_cfg, 'claude', None)
        if claude_cfg is None:
            raise RuntimeError(
                'agent_backend=claude requires a claude configuration block; '
                'rebuild the configuration template'
            )
        repository_root_path = str(getattr(open_cfg, 'repository_root_path', '') or '').strip()
        return ClaudeCliClient(
            binary=str(getattr(claude_cfg, 'binary', '') or ''),
            model=str(getattr(claude_cfg, 'model', '') or ''),
            max_turns=getattr(claude_cfg, 'max_turns', None),
            effort=str(getattr(claude_cfg, 'effort', '') or ''),
            allowed_tools=str(getattr(claude_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(claude_cfg, 'disallowed_tools', '') or ''),
            bypass_permissions=bool(getattr(claude_cfg, 'bypass_permissions', False)),
            docker_mode_on=self._docker_mode_on,
            read_only_tools_on=self._read_only_tools_on,
            timeout_seconds=int(getattr(claude_cfg, 'timeout_seconds', 1800) or 1800),
            max_retries=self._max_retries,
            repository_root_path=repository_root_path,
            model_smoke_test_enabled=(
                not self._testing
                and bool(getattr(claude_cfg, 'model_smoke_test_enabled', False))
            ),
            architecture_doc_path=str(
                getattr(claude_cfg, 'architecture_doc_path', '') or ''
            ),
            lessons_path=str(
                getattr(claude_cfg, 'lessons_path', '') or ''
            ),
        )

    def _build_codex(self, open_cfg: Any) -> AgentProvider:
        # Imported lazily so a Claude-only / OpenHands-only install
        # never touches the Codex module tree.
        from codex_core_lib.codex_core_lib.cli_client import CodexCliClient

        codex_cfg = getattr(open_cfg, 'codex', None)
        if codex_cfg is None:
            raise RuntimeError(
                'agent_backend=codex requires a codex configuration block; '
                'rebuild the configuration template'
            )
        repository_root_path = str(getattr(open_cfg, 'repository_root_path', '') or '').strip()
        return CodexCliClient(
            binary=str(getattr(codex_cfg, 'binary', '') or ''),
            model=str(getattr(codex_cfg, 'model', '') or ''),
            max_turns=getattr(codex_cfg, 'max_turns', None),
            effort=str(getattr(codex_cfg, 'effort', '') or ''),
            allowed_tools=str(getattr(codex_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(codex_cfg, 'disallowed_tools', '') or ''),
            bypass_permissions=bool(getattr(codex_cfg, 'bypass_permissions', False)),
            docker_mode_on=self._docker_mode_on,
            read_only_tools_on=self._read_only_tools_on,
            timeout_seconds=int(getattr(codex_cfg, 'timeout_seconds', 1800) or 1800),
            max_retries=self._max_retries,
            repository_root_path=repository_root_path,
            model_smoke_test_enabled=(
                not self._testing
                and bool(getattr(codex_cfg, 'model_smoke_test_enabled', False))
            ),
            architecture_doc_path=str(
                getattr(codex_cfg, 'architecture_doc_path', '') or ''
            ),
            lessons_path=str(
                getattr(codex_cfg, 'lessons_path', '') or ''
            ),
        )

    def _build_openhands(self, open_cfg: Any) -> AgentProvider:
        # Imported lazily — see _build_claude for the rationale.
        from openhands_core_lib.openhands_core_lib.openhands_client import OpenHandsClient
        from openhands_core_lib.openhands_core_lib.config_utils import (
            resolved_openhands_base_url,
            resolved_openhands_llm_settings,
        )

        openhands_cfg = getattr(open_cfg, 'openhands', None)
        if openhands_cfg is None:
            raise RuntimeError(
                'agent_backend=openhands requires an openhands configuration block; '
                'rebuild the configuration template'
            )
        return OpenHandsClient(
            resolved_openhands_base_url(openhands_cfg, testing=self._testing),
            openhands_cfg.api_key,
            self._max_retries,
            llm_settings=resolved_openhands_llm_settings(
                openhands_cfg,
                testing=self._testing,
            ),
            poll_interval_seconds=float(openhands_cfg.get('poll_interval_seconds', 2.0)),
            max_poll_attempts=int(openhands_cfg.get('max_poll_attempts', 900)),
            model_smoke_test_enabled=(
                not self._testing
                and bool(getattr(openhands_cfg, 'model_smoke_test_enabled', True))
            ),
        )
