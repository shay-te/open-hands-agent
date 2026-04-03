from collections.abc import Sequence
import json
from importlib import resources

from core_lib.data_layers.service.service import Service
from jinja2 import Template

from openhands_agent.data_layers.data.task import Task
from openhands_agent.helpers.error_handling_utils import run_best_effort
from openhands_agent.data_layers.data.fields import EmailFields, PullRequestFields
from openhands_agent.helpers.logging_utils import configure_logger


class NotificationService(Service):
    def __init__(
        self,
        app_name: str,
        email_core_lib,
        failure_email_cfg=None,
        completion_email_cfg=None,
    ) -> None:
        assert email_core_lib is not None, 'email_core_lib is required'
        assert failure_email_cfg is not None, 'failure_email_cfg is required'
        assert completion_email_cfg is not None, 'completion_email_cfg is required'
        self._app_name = app_name
        self._email_core_lib = email_core_lib
        self._failure_email_cfg = failure_email_cfg
        self._completion_email_cfg = completion_email_cfg
        self.logger = configure_logger(self.__class__.__name__)

    def notify_failure(
        self,
        operation: str,
        error: Exception,
        context: dict | None = None,
    ) -> bool:
        template_params = {
            EmailFields.OPERATION: operation,
            EmailFields.ERROR: str(error),
            EmailFields.CONTEXT: json.dumps(context or {}, default=str),
        }
        return self._send_templated_email(
            self._failure_email_cfg,
            subject=f'{self._app_name} failure: {operation}',
            default_template_name='failure_email.j2',
            template_params=template_params,
        )

    def notify_task_ready_for_review(
        self,
        task: Task,
        pull_requests,
    ) -> bool:
        normalized_pull_requests = self._normalized_pull_requests(pull_requests)
        first_pull_request = normalized_pull_requests[0] if normalized_pull_requests else {}
        template_params = {
            EmailFields.TASK_ID: task.id,
            EmailFields.TASK_SUMMARY: task.summary,
            EmailFields.PULL_REQUEST_URL: first_pull_request.get(PullRequestFields.URL, ''),
            EmailFields.PULL_REQUEST_TITLE: first_pull_request.get(PullRequestFields.TITLE, ''),
            EmailFields.PULL_REQUEST_SUMMARY: self._pull_request_summary(normalized_pull_requests),
        }
        return self._send_templated_email(
            self._completion_email_cfg,
            subject=f'Task ready for review: {task.id}',
            default_template_name='completion_email.j2',
            template_params=template_params,
        )

    def _send_configured_email(self, email_cfg, params: dict[str, str]) -> bool:
        recipients = self._normalized_recipients(getattr(email_cfg, 'recipients', []))
        template_id = getattr(email_cfg, 'template_id', None)
        if not recipients or not template_id:
            return False

        sender_info = self._sender_info(email_cfg)
        sent = False
        for recipient in recipients:
            send_result = run_best_effort(
                lambda recipient=recipient: self._email_core_lib.send(
                    template_id,
                    {
                        EmailFields.EMAIL: recipient,
                        **params,
                    },
                    sender_info,
                ),
                logger=self.logger,
                failure_log_message='failed to send email notification to %s',
                failure_args=(recipient,),
                default=False,
            )
            if send_result:
                sent = True
        return sent

    def _send_templated_email(
        self,
        email_cfg,
        *,
        subject: str,
        default_template_name: str,
        template_params: dict[str, str],
    ) -> bool:
        return self._send_configured_email(
            email_cfg,
            {
                EmailFields.SUBJECT: subject,
                **template_params,
                EmailFields.MESSAGE: self._render_template(
                    email_cfg,
                    default_template_name,
                    template_params,
                ),
            },
        )

    @staticmethod
    def _normalized_recipients(recipients) -> list[str]:
        if isinstance(recipients, str):
            return [recipients] if recipients else []
        if not isinstance(recipients, (list, tuple, set, Sequence)):
            return []
        return [str(recipient) for recipient in recipients if recipient]

    @staticmethod
    def _sender_info(email_cfg):
        sender_cfg = getattr(email_cfg, 'sender', None)
        if not sender_cfg:
            return None
        return {
            'name': getattr(sender_cfg, 'name', ''),
            'email': getattr(sender_cfg, 'email', ''),
        }

    def _render_template(
        self,
        email_cfg,
        default_template_name: str,
        template_params: dict[str, str],
    ) -> str:
        template_name = (
            getattr(email_cfg, 'body_template', default_template_name)
            if email_cfg
            else default_template_name
        )
        try:
            template_path = resources.files('openhands_agent.templates.email').joinpath(
                template_name
            )
            template_text = template_path.read_text(encoding='utf-8')
        except (FileNotFoundError, OSError):
            self.logger.exception('failed to load email template %s', template_name)
            return ''
        return Template(template_text).render(**template_params).rstrip('\n')

    @staticmethod
    def _normalized_pull_requests(pull_requests) -> list[dict[str, str]]:
        if isinstance(pull_requests, dict):
            return [pull_requests]
        if not isinstance(pull_requests, list):
            return []
        return [pull_request for pull_request in pull_requests if isinstance(pull_request, dict)]

    @staticmethod
    def _pull_request_summary(pull_requests: list[dict[str, str]]) -> str:
        lines = []
        for pull_request in pull_requests:
            repository_label = pull_request.get(PullRequestFields.REPOSITORY_ID, 'repository')
            title = pull_request.get(PullRequestFields.TITLE, '')
            url = pull_request.get(PullRequestFields.URL, '')
            entry_lines = [f'- {repository_label}: {title}']
            if url:
                entry_lines.append(url)
            lines.append('\n'.join(entry_lines))
        return '\n\n'.join(lines)
