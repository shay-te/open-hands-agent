from __future__ import annotations

from openhands_agent.helpers.text_utils import normalized_text


GENERATED_ARTIFACT_ROOTS = {'build', 'dist', 'out', 'coverage', 'target'}


def status_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if ' -> ' in path:
            path = path.split(' -> ', 1)[1]
        normalized_path = normalized_text(path).rstrip('/')
        if normalized_path:
            paths.append(normalized_path)
    return paths


def validation_report_paths_from_status(status_output: str) -> list[str]:
    return [
        path
        for path in status_paths(status_output)
        if path.endswith('validation_report.md')
    ]


def generated_artifact_paths_from_status(status_output: str) -> list[str]:
    generated_artifact_paths: list[str] = []
    for path in status_paths(status_output):
        if path.endswith('validation_report.md'):
            continue
        path_root = path.split('/', 1)[0]
        if path_root not in GENERATED_ARTIFACT_ROOTS:
            continue
        if path_root not in generated_artifact_paths:
            generated_artifact_paths.append(path_root)
    return generated_artifact_paths


def status_contains_only_removable_artifacts(
    status_output: str,
    generated_artifact_paths: list[str],
    validation_report_paths: list[str],
) -> bool:
    removable_roots = set(generated_artifact_paths)
    removable_reports = set(validation_report_paths)
    for path in status_paths(status_output):
        if path in removable_reports:
            continue
        path_root = path.split('/', 1)[0]
        if path_root in removable_roots:
            continue
        return False
    return True


def git_ready_command_summary(
    destination_branch: str,
    *,
    include_remote_sync: bool,
) -> str:
    commands = [f'git checkout -f {destination_branch}']
    if include_remote_sync:
        commands.insert(0, 'git fetch origin')
        commands.append(f'git reset --hard origin/{destination_branch}')
    commands.append('git clean -fd')
    return ' && '.join(commands)
