from __future__ import annotations

# Re-exported from the shared base so every provider issue client and the
# shared IssueClientBase agree on the neutral comment-entry dict shape.
from provider_client_base.provider_client_base.data.issue_record import (  # noqa: F401
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)


class GitLabIssueFields(object):
    IID = 'iid'
    TITLE = 'title'
    DESCRIPTION = 'description'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    USERNAME = 'username'
    NAME = 'name'


class GitLabCommentFields(object):
    BODY = 'body'
    AUTHOR = 'author'
    USERNAME = 'username'
    NAME = 'name'
    SYSTEM = 'system'
