class PullRequestFields:
    ID = 'id'
    TITLE = 'title'
    URL = 'url'
    SOURCE_BRANCH = 'source_branch'
    DESTINATION_BRANCH = 'destination_branch'
    DESCRIPTION = 'description'
    REPOSITORY_ID = 'repository_id'
    PULL_REQUESTS = 'pull_requests'
    FAILED_REPOSITORIES = 'failed_repositories'


class ReviewCommentFields:
    PULL_REQUEST_ID = 'pull_request_id'
    COMMENT_ID = 'comment_id'
    AUTHOR = 'author'
    BODY = 'body'
    ALL_COMMENTS = 'all_comments'
    RESOLUTION_TARGET_ID = 'resolution_target_id'
    RESOLUTION_TARGET_TYPE = 'resolution_target_type'
    RESOLVABLE = 'resolvable'


class TaskCommentFields:
    AUTHOR = 'author'
    BODY = 'body'
    ALL_COMMENTS = 'all_comments'


class ImplementationFields:
    COMMIT_MESSAGE = 'commit_message'
    MESSAGE = 'message'
    SESSION_ID = 'session_id'
    SUCCESS = 'success'


class StatusFields:
    STATUS = 'status'
    UPDATED = 'updated'
    READY_FOR_REVIEW = 'ready_for_review'
    PARTIAL_FAILURE = 'partial_failure'
    SKIPPED = 'skipped'
    TESTING_FAILED = 'testing_failed'


class TaskFields:
    ID = 'task_id'
    SUMMARY = 'task_summary'


class EmailFields:
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


class RepositoryFields:
    ID = 'id'
    DISPLAY_NAME = 'display_name'
    LOCAL_PATH = 'local_path'
    PROVIDER_BASE_URL = 'provider_base_url'
    OWNER = 'owner'
    REPO_SLUG = 'repo_slug'
    DESTINATION_BRANCH = 'destination_branch'
    ALIASES = 'aliases'


class YouTrackAttachmentFields:
    ID = 'id'
    NAME = 'name'
    MIME_TYPE = 'mimeType'
    CHARSET = 'charset'
    METADATA = 'metaData'
    URL = 'url'


class YouTrackCommentFields:
    ID = 'id'
    TEXT = 'text'
    AUTHOR = 'author'
    LOGIN = 'login'
    NAME = 'name'


class YouTrackCustomFieldFields:
    ID = 'id'
    NAME = 'name'
    TYPE = '$type'


class JiraIssueFields:
    KEY = 'key'
    FIELDS = 'fields'
    SUMMARY = 'summary'
    DESCRIPTION = 'description'
    COMMENT = 'comment'
    ATTACHMENT = 'attachment'
    STATUS = 'status'


class JiraCommentFields:
    BODY = 'body'
    AUTHOR = 'author'
    DISPLAY_NAME = 'displayName'


class JiraAttachmentFields:
    FILENAME = 'filename'
    MIME_TYPE = 'mimeType'
    CONTENT = 'content'
    SIZE = 'size'


class JiraTransitionFields:
    ID = 'id'
    NAME = 'name'
    TO = 'to'


class GitHubIssueFields:
    NUMBER = 'number'
    TITLE = 'title'
    BODY = 'body'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    LOGIN = 'login'
    PULL_REQUEST = 'pull_request'
    NAME = 'name'


class GitHubCommentFields:
    BODY = 'body'
    USER = 'user'
    LOGIN = 'login'


class GitLabIssueFields:
    IID = 'iid'
    TITLE = 'title'
    DESCRIPTION = 'description'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    USERNAME = 'username'
    NAME = 'name'


class GitLabCommentFields:
    BODY = 'body'
    AUTHOR = 'author'
    USERNAME = 'username'
    NAME = 'name'
    SYSTEM = 'system'


class BitbucketIssueFields:
    ID = 'id'
    TITLE = 'title'
    CONTENT = 'content'
    RAW = 'raw'
    STATE = 'state'
    ASSIGNEE = 'assignee'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'


class BitbucketIssueCommentFields:
    CONTENT = 'content'
    RAW = 'raw'
    USER = 'user'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'
