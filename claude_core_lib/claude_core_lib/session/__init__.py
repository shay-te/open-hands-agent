"""Session machinery for the Claude backend.

Groups everything that participates in keeping a per-task Claude
conversation alive across kato spawns: the manager that tracks
``task_id → agent_session_id``, the long-lived streaming subprocess
wrapper, the on-disk JSONL transcript readers, and the wire-protocol
event constants.

One-shot Claude calls (``cli_client.ClaudeCliClient``) live one level
up — they don't persist or resume.
"""
