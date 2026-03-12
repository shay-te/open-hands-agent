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


class ImplementationFields:
    COMMIT_MESSAGE = 'commit_message'
    SUCCESS = 'success'


class StatusFields:
    STATUS = 'status'
    UPDATED = 'updated'
    READY_FOR_REVIEW = 'ready_for_review'
    PARTIAL_FAILURE = 'partial_failure'
    SKIPPED = 'skipped'
    TESTING_FAILED = 'testing_failed'


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
