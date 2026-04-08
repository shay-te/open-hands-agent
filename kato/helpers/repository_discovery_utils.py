from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import quote, urlparse


DISCOVERY_SKIP_DIRS = {
    '.git',
    '.hg',
    '.svn',
    '.venv',
    '__pycache__',
    'node_modules',
}

@dataclass(frozen=True)
class DiscoveredRepository:
    local_path: str
    remote_url: str
    provider: str
    owner: str
    repo_slug: str


def discover_git_repositories(
    projects_root: str,
    ignored_folders=None,
) -> list[DiscoveredRepository]:
    root_path = Path(projects_root).expanduser()
    if not root_path.exists() or not root_path.is_dir():
        return []

    ignored_folder_names = {
        str(folder).strip().lower()
        for folder in (ignored_folders or [])
        if str(folder).strip()
    }
    repositories: list[DiscoveredRepository] = []
    for current_root, dir_names, file_names in os.walk(root_path):
        has_git_metadata = '.git' in dir_names or '.git' in file_names
        dir_names[:] = [
            directory
            for directory in dir_names
            if directory not in DISCOVERY_SKIP_DIRS
            and directory.lower() not in ignored_folder_names
        ]
        if not has_git_metadata:
            continue
        repository_path = Path(current_root).resolve()
        repositories.append(build_discovered_repository(repository_path))
        dir_names[:] = []

    repositories.sort(key=lambda repository: repository.local_path.lower())
    return repositories


def build_discovered_repository(repository_path: Path) -> DiscoveredRepository:
    remote_url = read_git_remote_url(repository_path)
    provider, owner, repo_slug = parse_git_remote_url(remote_url)
    return DiscoveredRepository(
        local_path=str(repository_path),
        remote_url=remote_url,
        provider=provider,
        owner=owner,
        repo_slug=repo_slug,
    )


def read_git_remote_url(repository_path: Path) -> str:
    config_path = git_config_path(repository_path)
    if config_path is None or not config_path.exists():
        return ''

    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(config_path, encoding='utf-8')
    except configparser.Error:
        return ''
    if parser.has_option('remote "origin"', 'url'):
        return parser.get('remote "origin"', 'url').strip()

    for section in parser.sections():
        if section.startswith('remote "') and parser.has_option(section, 'url'):
            return parser.get(section, 'url').strip()
    return ''


def git_config_path(repository_path: Path) -> Path | None:
    git_entry = repository_path / '.git'
    if git_entry.is_dir():
        return git_entry / 'config'
    if not git_entry.is_file():
        return None

    git_file_lines = git_entry.read_text(encoding='utf-8').splitlines()
    if not git_file_lines:
        return None

    first_line = git_file_lines[0].strip()
    if not first_line.startswith('gitdir:'):
        return None
    git_dir = first_line.split(':', 1)[1].strip()
    git_dir_path = Path(git_dir)
    if not git_dir_path.is_absolute():
        git_dir_path = (repository_path / git_dir_path).resolve()
    return git_dir_path / 'config'


def parse_git_remote_url(remote_url: str) -> tuple[str, str, str]:
    if not remote_url:
        return '', '', ''

    host = ''
    path = ''
    if '://' in remote_url:
        parsed = urlparse(remote_url)
        host = str(parsed.hostname or '').lower()
        path = parsed.path.lstrip('/')
    else:
        match = re.match(r'[^@]+@([^:]+):(.+)', remote_url)
        if match is not None:
            host = match.group(1).lower()
            path = match.group(2)

    if not host or not path:
        return '', '', ''

    path = path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2:
        return '', '', ''

    provider = ''
    if 'github' in host:
        provider = 'github'
    elif 'gitlab' in host:
        provider = 'gitlab'
    elif 'bitbucket' in host:
        provider = 'bitbucket'
    return provider, '/'.join(parts[:-1]), parts[-1]


def repository_id_from_name(name: str) -> str:
    normalized = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower())
    return normalized.strip('-') or 'primary'


def display_name_from_repo_slug(repo_slug: str) -> str:
    words = [part for part in re.split(r'[-_]+', repo_slug.strip()) if part]
    if not words:
        return 'Primary Repository'
    return ' '.join(word[:1].upper() + word[1:] for word in words)


def remote_web_base_url(remote_url: str) -> str:
    if not remote_url:
        return ''
    if '://' in remote_url:
        parsed = urlparse(remote_url)
        if not parsed.hostname:
            return ''
        scheme = parsed.scheme or 'https'
        port = f':{parsed.port}' if parsed.port else ''
        return f'{scheme}://{parsed.hostname}{port}'

    match = re.match(r'[^@]+@([^:]+):(.+)', remote_url)
    if match is None:
        return ''
    return f'https://{match.group(1)}'


def review_url_for_remote(
    remote_url: str,
    provider: str,
    owner: str,
    repo_slug: str,
    source_branch: str,
    destination_branch: str,
) -> str:
    web_base_url = remote_web_base_url(remote_url)
    if not web_base_url or not owner or not repo_slug:
        return ''

    repository_path = f'{owner}/{repo_slug}'.strip('/')
    if provider == 'github':
        return (
            f'{web_base_url}/{repository_path}/compare/'
            f'{quote(destination_branch, safe="")}...{quote(source_branch, safe="")}?expand=1'
        )
    if provider == 'gitlab':
        return (
            f'{web_base_url}/{repository_path}/-/merge_requests/new'
            f'?merge_request[source_branch]={quote(source_branch, safe="")}'
            f'&merge_request[target_branch]={quote(destination_branch, safe="")}'
        )
    if provider == 'bitbucket':
        return (
            f'{web_base_url}/{repository_path}/pull-requests/new'
            f'?source={quote(source_branch, safe="")}&dest={quote(destination_branch, safe="")}'
        )
    return f'{web_base_url}/{repository_path}'
