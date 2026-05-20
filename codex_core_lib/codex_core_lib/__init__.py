"""Codex Code CLI backend for kato.

Mirrors the structure of ``claude_core_lib`` so a reader who has
learned one backend recognises the other. Shared helpers
(``agent_prompt_utils``, ``architecture_doc_utils``, …) live in
``agent_core_lib`` and are re-exported here under the same module
names so the two backends present the same import surface to the
orchestration layer.
"""

from codex_core_lib.codex_core_lib.cli_client import CodexCliClient  # noqa: F401
