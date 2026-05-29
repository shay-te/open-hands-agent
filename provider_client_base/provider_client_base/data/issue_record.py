from __future__ import annotations

from dataclasses import dataclass, field

# Neutral comment-entry / record field keys shared by every provider
# issue client (bitbucket / github / gitlab / jira). Kept here so the
# shared ``IssueClientBase`` helpers and each provider client agree on
# the dict shape without re-declaring the constants per lib.
ISSUE_COMMENT_AUTHOR = 'author'
ISSUE_COMMENT_BODY = 'body'
ISSUE_ALL_COMMENTS = 'all_comments'


@dataclass
class IssueRecord:
    """Neutral data transfer object for one provider issue.

    Returned by every provider's ``get_assigned_tasks``. Field names
    mirror the kato ``Task`` interface so duck-typed orchestrators can
    use it without an explicit conversion step.
    """

    id: str = ''
    summary: str = ''
    description: str = ''
    branch_name: str = ''
    tags: list[str] = field(default_factory=list)
    all_comments: list[dict] = field(default_factory=list)
