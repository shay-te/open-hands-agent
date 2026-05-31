"""Shared output-side credential / phishing scan for agent responses.

A one-shot agent client and a streaming agent session run an identical
detective scan over the agent's final response text: two pattern families
fire and each emits a WARNING audit line. The scan is detective-only — the
agent's text has already crossed to the model provider by the time the
result lands, so the log is an auditable record (rotate / treat-as-
untrusted), never a block.

Pattern names + redacted previews are logged; full credential values are
never logged. See ``BYPASS_PROTECTIONS.md`` residuals #16 (phishing) and
#18 (credential exfil).

This helper lives in ``agent_core_lib`` so any agent client can reuse it;
the ``sandbox_core_lib`` detectors are imported lazily inside the function
so callers keep delegating without an import-time dependency.
"""

from __future__ import annotations


def scan_text_for_credentials_and_phishing(
    text: str,
    *,
    logger,
    context_label: str,
) -> None:
    """WARNING-log credential AND phishing patterns found in ``text``.

    Two pattern families fire:

      * **Credential patterns** (residual #18) — pattern name +
        redacted preview only; the full credential value is never
        logged. Operators who see this should rotate the named
        credential. The agent's text has already crossed to the model
        provider by the time the result returns, so this is an audit
        trail not a block.
      * **Phishing patterns** (residual #16, defense-in-depth) — agent
        output that looks like an attempt to trick the operator into
        running shell commands on their host (``curl|bash``, ``sudo``
        snippets, ``eval $(curl …)``). Same audit-trail treatment.

    ``context_label`` is the descriptor woven into the WARNING messages
    (e.g. ``'Claude response for triage investigation'`` or
    ``'streaming Claude session for task PROJ-1'``). Blank ``text`` is a
    no-op.
    """
    from sandbox_core_lib.sandbox_core_lib.credential_patterns import (
        find_credential_patterns,
        find_phishing_patterns,
        summarize_findings,
    )

    if not text:
        return
    cred_findings = find_credential_patterns(text)
    if cred_findings:
        logger.warning(
            'CREDENTIAL PATTERN DETECTED in %s: %s. '
            'The agent response has already been transmitted to the model '
            'provider; rotate the named credential(s) immediately. See '
            'BYPASS_PROTECTIONS.md residual #18.',
            context_label,
            summarize_findings(cred_findings),
        )
    phishing_findings = find_phishing_patterns(text)
    if phishing_findings:
        logger.warning(
            'PHISHING PATTERN DETECTED in %s: %s. '
            'The agent appears to be instructing the operator to run '
            'shell commands on their host. The agent has no legitimate '
            'reason to direct the operator to execute commands. Treat the '
            'suggestion as untrusted. See BYPASS_PROTECTIONS.md residual #16.',
            context_label,
            summarize_findings(phishing_findings),
        )
