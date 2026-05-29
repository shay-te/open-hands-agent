from __future__ import annotations

# Re-exported from the shared base so every provider issue client and the
# shared IssueClientBase agree on the neutral comment-entry dict shape.
from provider_client_base.provider_client_base.data.issue_record import (  # noqa: F401
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)


class BitbucketIssueFields(object):
    ID = 'id'
    TITLE = 'title'
    CONTENT = 'content'
    RAW = 'raw'
    STATE = 'state'
    ASSIGNEE = 'assignee'
    LABELS = 'labels'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'


class BitbucketIssueCommentFields(object):
    CONTENT = 'content'
    RAW = 'raw'
    USER = 'user'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'
