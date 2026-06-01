"""Builders and parsers for kato's namespaced task tags.

Every kato-recognized task tag lives under the ``kato:`` namespace
(``KATO_TAG_NAMESPACE``). Build and inspect tags through these helpers instead
of hand-writing ``'kato:...'`` strings, so the namespace and the segment names
(``repo``, ``triage``, ...) have exactly one definition. The raw constants live
in :mod:`kato_core_lib.data_layers.data.fields` (``RepositoryFields`` /
``TaskTags``); this module composes them.

There is a mirror of the repository-tag helpers on the web client at
``webserver/ui/src/utils/katoTags.js`` — keep the two in sync.
"""
from __future__ import annotations

from kato_core_lib.data_layers.data.fields import (
    RepositoryFields,
    TaskTags,
)
from kato_core_lib.helpers.text_utils import normalized_text


def build_repository_tag(repo_id: object) -> str:
    """Return the ``kato:repo:<repo_id>`` tag for a repository folder name."""
    return f'{RepositoryFields.REPOSITORY_TAG_PREFIX}{normalized_text(repo_id)}'


def repository_id_from_tag(tag: object) -> str:
    """Return the repo id from a ``kato:repo:<id>`` tag, preserving its case.

    Returns ``''`` for anything that isn't a repository tag or whose value
    after the prefix is blank — callers filter on the truthy result.
    """
    text = normalized_text(tag)
    if not text.lower().startswith(RepositoryFields.REPOSITORY_TAG_PREFIX):
        return ''
    return normalized_text(text[len(RepositoryFields.REPOSITORY_TAG_PREFIX):])


def build_triage_tag(outcome: object) -> str:
    """Return the ``kato:triage:<outcome>`` tag (e.g. ``'high'`` -> ``'kato:triage:high'``)."""
    return f'{TaskTags.TRIAGE_PREFIX}{normalized_text(outcome)}'
