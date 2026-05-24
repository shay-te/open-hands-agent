from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from provider_client_base.provider_client_base.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)


class TaskCommentFields(object):
    AUTHOR = 'author'
    BODY = 'body'
    ALL_COMMENTS = 'all_comments'


class StatusFields(object):
    STATUS = 'status'
    UPDATED = 'updated'
    READY_FOR_REVIEW = 'ready_for_review'
    PARTIAL_FAILURE = 'partial_failure'
    NO_CHANGES = 'no_changes'
    SKIPPED = 'skipped'
    TESTING_FAILED = 'testing_failed'


class TaskFields(object):
    ID = 'task_id'
    SUMMARY = 'task_summary'


class EmailFields(object):
    EMAIL = 'email'
    SUBJECT = 'subject'
    MESSAGE = 'message'
    OPERATION = 'operation'
    ERROR = 'error'
    CONTEXT = 'context'
    TASK_ID = 'task_id'
    TASK_SUMMARY = 'task_summary'
    PULL_REQUEST_TITLE = 'pull_request_title'
    PULL_REQUEST_URL = 'pull_request_url'
    PULL_REQUEST_SUMMARY = 'pull_request_summary'


class RepositoryFields(object):
    ID = 'id'
    DISPLAY_NAME = 'display_name'
    LOCAL_PATH = 'local_path'
    PROVIDER_BASE_URL = 'provider_base_url'
    OWNER = 'owner'
    REPO_SLUG = 'repo_slug'
    DESTINATION_BRANCH = 'destination_branch'
    BITBUCKET_USERNAME = 'bitbucket_username'
    BITBUCKET_API_EMAIL = 'bitbucket_api_email'
    ALIASES = 'aliases'
    REPOSITORY_TAG_PREFIX = 'kato:repo:'


class TaskTags(object):
    """Kato-recognized task tag prefixes/values.

    Tags are namespaced with the ``kato:`` prefix so they don't collide with
    user-defined ticket labels.
    """

    WAIT_PLANNING = 'kato:wait-planning'

    # Hold-before-publish gate. When this tag is on a task, kato runs
    # the agent and commits to the local task branch as usual, but
    # stops before pushing the branch and opening the PR. Removing the
    # tag (via the planning UI's "Approve push" button or the platform
    # tag UI) lets the next scan tick proceed with publish. Kato — not
    # Claude — performs the push when approved.
    WAIT_BEFORE_GIT_PUSH = 'kato:wait-before-git-push'

    # Triage workflow: when ``TRIAGE_INVESTIGATE`` is on a task, kato
    # spends one Claude turn investigating (read-only — no edits, no
    # PRs) and replaces it with one of the outcome tags below. The
    # original triage tag is removed once the outcome tag lands.
    TRIAGE_INVESTIGATE = 'kato:triage:investigate'
    TRIAGE_PREFIX = 'kato:triage:'

    # Priority/urgency outcomes — the issue is real, kato classifies
    # how soon it should be worked.
    TRIAGE_CRITICAL = 'kato:triage:critical'
    TRIAGE_HIGH = 'kato:triage:high'
    TRIAGE_MEDIUM = 'kato:triage:medium'
    TRIAGE_LOW = 'kato:triage:low'

    # Disposition outcomes — the issue won't be worked as-is.
    TRIAGE_DUPLICATE = 'kato:triage:duplicate'
    TRIAGE_WONTFIX = 'kato:triage:wontfix'
    TRIAGE_INVALID = 'kato:triage:invalid'
    TRIAGE_NEEDS_INFO = 'kato:triage:needs-info'

    # Optional extras.
    TRIAGE_BLOCKED = 'kato:triage:blocked'
    TRIAGE_QUESTION = 'kato:triage:question'


TRIAGE_OUTCOME_TAGS = (
    TaskTags.TRIAGE_CRITICAL,
    TaskTags.TRIAGE_HIGH,
    TaskTags.TRIAGE_MEDIUM,
    TaskTags.TRIAGE_LOW,
    TaskTags.TRIAGE_DUPLICATE,
    TaskTags.TRIAGE_WONTFIX,
    TaskTags.TRIAGE_INVALID,
    TaskTags.TRIAGE_NEEDS_INFO,
    TaskTags.TRIAGE_BLOCKED,
    TaskTags.TRIAGE_QUESTION,
)


class YouTrackAttachmentFields(object):
    ID = 'id'
    NAME = 'name'
    MIME_TYPE = 'mimeType'
    CHARSET = 'charset'
    METADATA = 'metaData'
    URL = 'url'


class YouTrackCommentFields(object):
    ID = 'id'
    TEXT = 'text'
    AUTHOR = 'author'
    LOGIN = 'login'
    NAME = 'name'


class YouTrackCustomFieldFields(object):
    ID = 'id'
    NAME = 'name'
    TYPE = '$type'


class YouTrackTagFields(object):
    NAME = 'name'


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
