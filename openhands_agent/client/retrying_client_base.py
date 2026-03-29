from core_lib.client.client_base import ClientBase

from openhands_agent.logging_utils import configure_logger
from openhands_agent.client.retry_utils import run_with_retry


class RetryingClientBase(ClientBase):
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url.rstrip('/'))
        self.logger = configure_logger(self.__class__.__name__)
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(timeout)
        self.max_retries = max(1, max_retries)

    def _get_with_retry(self, path: str, **kwargs):
        return run_with_retry(lambda: self._get(path, **kwargs), self.max_retries)

    def _post_with_retry(self, path: str, **kwargs):
        return run_with_retry(lambda: self._post(path, **kwargs), self.max_retries)

    def _put_with_retry(self, path: str, **kwargs):
        return run_with_retry(lambda: self._put(path, **kwargs), self.max_retries)

    def _patch(self, path: str, **kwargs):
        url = f'{self.base_url.rstrip("/")}/{path.lstrip("/")}'
        return self.session.patch(url, **self.process_kwargs(**kwargs))

    def _patch_with_retry(self, path: str, **kwargs):
        return run_with_retry(lambda: self._patch(path, **kwargs), self.max_retries)
