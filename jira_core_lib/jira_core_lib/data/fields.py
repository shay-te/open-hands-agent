from __future__ import annotations

# Re-exported from the shared base so every provider issue client and the
# shared IssueClientBase agree on the neutral comment-entry dict shape.
from provider_client_base.provider_client_base.data.issue_record import (  # noqa: F401
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)


class JiraIssueFields(object):
    KEY = 'key'
    FIELDS = 'fields'
    SUMMARY = 'summary'
    DESCRIPTION = 'description'
    COMMENT = 'comment'
    ATTACHMENT = 'attachment'
    LABELS = 'labels'
    STATUS = 'status'


class JiraCommentFields(object):
    BODY = 'body'
    AUTHOR = 'author'
    DISPLAY_NAME = 'displayName'


class JiraAttachmentFields(object):
    FILENAME = 'filename'
    MIME_TYPE = 'mimeType'
    CONTENT = 'content'
    SIZE = 'size'


class JiraTransitionFields(object):
    ID = 'id'
    NAME = 'name'
    TO = 'to'
