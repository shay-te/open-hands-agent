from __future__ import annotations

# Re-exported from the shared base so every provider issue client and the
# shared IssueClientBase agree on the neutral comment-entry dict shape.
from provider_client_base.provider_client_base.data.issue_record import (  # noqa: F401
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)


class GitHubIssueFields(object):
    NUMBER = 'number'
    TITLE = 'title'
    BODY = 'body'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    LOGIN = 'login'
    PULL_REQUEST = 'pull_request'
    NAME = 'name'


class GitHubCommentFields(object):
    BODY = 'body'
    USER = 'user'
    LOGIN = 'login'
