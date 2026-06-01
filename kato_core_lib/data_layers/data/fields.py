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


# Every kato-recognized task tag is namespaced under ``kato:`` so it can't
# collide with user-defined ticket labels. These segment names are the single
# source of truth — build/parse tags via ``kato_core_lib.helpers.kato_tag_utils``
# instead of hand-writing ``kato:...`` strings.
KATO_TAG_NAMESPACE = 'kato'


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
    # ``kato:repo:<repo-folder-name>`` — names a repository for a task.
    REPOSITORY_TAG_SEGMENT = 'repo'
    REPOSITORY_TAG_PREFIX = f'{KATO_TAG_NAMESPACE}:{REPOSITORY_TAG_SEGMENT}:'


class TaskTags(object):
    """Kato-recognized task tag prefixes/values.

    Every tag is namespaced under ``kato:`` (``KATO_TAG_NAMESPACE``) so it
    can't collide with user-defined ticket labels. The strings are built from
    the namespace + a segment rather than hand-written; build/parse them via
    ``kato_core_lib.helpers.kato_tag_utils``.
    """

    # Planning gate: hold a task in the planning UI before kato runs it.
    WAIT_PLANNING = f'{KATO_TAG_NAMESPACE}:wait-planning'

    # Hold-before-publish gate. When this tag is on a task, kato runs
    # the agent and commits to the local task branch as usual, but
    # stops before pushing the branch and opening the PR. Removing the
    # tag (via the planning UI's "Approve push" button or the platform
    # tag UI) lets the next scan tick proceed with publish. Kato — not
    # Claude — performs the push when approved.
    WAIT_BEFORE_GIT_PUSH = f'{KATO_TAG_NAMESPACE}:wait-before-git-push'

    # Triage workflow: when ``TRIAGE_INVESTIGATE`` is on a task, kato
    # spends one Claude turn investigating (read-only — no edits, no
    # PRs) and replaces it with one of the outcome tags below. The
    # original triage tag is removed once the outcome tag lands.
    TRIAGE_SEGMENT = 'triage'
    TRIAGE_PREFIX = f'{KATO_TAG_NAMESPACE}:{TRIAGE_SEGMENT}:'
    TRIAGE_INVESTIGATE = f'{TRIAGE_PREFIX}investigate'

    # Priority/urgency outcomes — the issue is real, kato classifies
    # how soon it should be worked.
    TRIAGE_CRITICAL = f'{TRIAGE_PREFIX}critical'
    TRIAGE_HIGH = f'{TRIAGE_PREFIX}high'
    TRIAGE_MEDIUM = f'{TRIAGE_PREFIX}medium'
    TRIAGE_LOW = f'{TRIAGE_PREFIX}low'

    # Disposition outcomes — the issue won't be worked as-is.
    TRIAGE_DUPLICATE = f'{TRIAGE_PREFIX}duplicate'
    TRIAGE_WONTFIX = f'{TRIAGE_PREFIX}wontfix'
    TRIAGE_INVALID = f'{TRIAGE_PREFIX}invalid'
    TRIAGE_NEEDS_INFO = f'{TRIAGE_PREFIX}needs-info'

    # Optional extras.
    TRIAGE_BLOCKED = f'{TRIAGE_PREFIX}blocked'
    TRIAGE_QUESTION = f'{TRIAGE_PREFIX}question'


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
