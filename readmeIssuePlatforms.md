# Issue Platforms — kato

Setup snippets for every supported issue/ticket platform.

## Supported Providers

The agent currently supports these issue trackers:

- YouTrack
- Jira
- GitHub Issues
- GitLab Issues
- Bitbucket Issues

The repository provider is inferred from the configured repository metadata, and the same task can span multiple repositories if the task text matches them.

## Third-Party Setup

Pick one issue platform with `KATO_ISSUE_PLATFORM`, then fill in the matching block below. Keep the other issue-platform blocks empty unless you are switching providers or using their repository API credentials for pull requests.

After editing `.env`, run:

```bash
kato doctor
```

## Setting Up YouTrack

Use this when tasks are coming from YouTrack:

```env
KATO_ISSUE_PLATFORM=youtrack
YOUTRACK_API_BASE_URL=https://your-company.youtrack.cloud
YOUTRACK_API_TOKEN=...
YOUTRACK_PROJECT=PROJ
YOUTRACK_ASSIGNEE=your-youtrack-login
YOUTRACK_ISSUE_STATES=Todo,Open
YOUTRACK_PROGRESS_STATE_FIELD=State
YOUTRACK_PROGRESS_STATE=In Progress
YOUTRACK_REVIEW_STATE_FIELD=State
YOUTRACK_REVIEW_STATE=To Verify
```

`YOUTRACK_ISSUE_STATES` is the queue Kato scans. The progress and review state settings tell Kato how to move the issue when work starts and when the pull request is ready.

## Setting Up Jira

Use this when tasks are coming from Jira:

```env
KATO_ISSUE_PLATFORM=jira
JIRA_API_BASE_URL=https://your-company.atlassian.net
JIRA_API_TOKEN=...
JIRA_EMAIL=you@example.com
JIRA_PROJECT=PROJ
JIRA_ASSIGNEE=assignee-account-id-or-username
JIRA_ISSUE_STATES=To Do,Open
JIRA_PROGRESS_STATE_FIELD=status
JIRA_PROGRESS_STATE=In Progress
JIRA_REVIEW_STATE_FIELD=status
JIRA_REVIEW_STATE=In Review
```

`JIRA_API_TOKEN` is the API token. Keep `JIRA_EMAIL` set for Atlassian authentication flows that need the account email.

## Setting Up GitHub Issues

Use this when tasks are coming from GitHub Issues:

```env
KATO_ISSUE_PLATFORM=github
GITHUB_API_BASE_URL=https://api.github.com
GITHUB_API_TOKEN=...
GITHUB_OWNER=owner-or-org
GITHUB_REPO=repo-name
GITHUB_ASSIGNEE=assignee-login
GITHUB_ISSUE_STATES=open
GITHUB_PROGRESS_STATE_FIELD=labels
GITHUB_PROGRESS_STATE=In Progress
GITHUB_REVIEW_STATE_FIELD=labels
GITHUB_REVIEW_STATE=In Review
```

`GITHUB_API_TOKEN` is also used for GitHub git push and pull request creation when discovered repositories live on GitHub.

## Setting Up GitLab Issues

Use this when tasks are coming from GitLab Issues:

```env
KATO_ISSUE_PLATFORM=gitlab
GITLAB_API_BASE_URL=https://gitlab.com/api/v4
GITLAB_API_TOKEN=...
GITLAB_PROJECT=group/project
GITLAB_ASSIGNEE=assignee-username
GITLAB_ISSUE_STATES=opened
GITLAB_PROGRESS_STATE_FIELD=labels
GITLAB_PROGRESS_STATE=In Progress
GITLAB_REVIEW_STATE_FIELD=labels
GITLAB_REVIEW_STATE=In Review
```

`GITLAB_API_TOKEN` is also used for GitLab git push and merge request creation when discovered repositories live on GitLab.

## Setting Up Bitbucket Issues

Use this when tasks are coming from Bitbucket Issues:

```env
KATO_ISSUE_PLATFORM=bitbucket
BITBUCKET_API_BASE_URL=https://api.bitbucket.org/2.0
BITBUCKET_API_TOKEN=...
BITBUCKET_USERNAME=bitbucket-username
BITBUCKET_API_EMAIL=you@example.com
BITBUCKET_WORKSPACE=workspace
BITBUCKET_REPO_SLUG=repo-slug
BITBUCKET_ASSIGNEE=assignee-username
BITBUCKET_ISSUE_STATES=new,open
BITBUCKET_PROGRESS_STATE_FIELD=state
BITBUCKET_PROGRESS_STATE=open
BITBUCKET_REVIEW_STATE_FIELD=state
BITBUCKET_REVIEW_STATE=resolved
```

`BITBUCKET_API_TOKEN` is used for Bitbucket git auth and REST API calls. `BITBUCKET_API_EMAIL` is required for Bitbucket pull request API auth.
