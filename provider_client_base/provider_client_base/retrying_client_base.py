from core_lib.client.client_base import ClientBase

from provider_client_base.provider_client_base.helpers.logging_utils import configure_logger
from provider_client_base.provider_client_base.helpers.retry_utils import run_with_retry


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

    def _abs_url(self, path: str) -> str:
        return f'{self.base_url.rstrip("/")}/{path.lstrip("/")}'

    def _request_with_retry(self, method: str, verb_callable, path: str, **kwargs):
        return run_with_retry(
            lambda: verb_callable(path, **kwargs),
            self.max_retries,
            operation_name=self._retry_operation_name(method, path),
        )

    def _get_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('GET', self._get, path, **kwargs)

    def _post_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('POST', self._post, path, **kwargs)

    def _put_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('PUT', self._put, path, **kwargs)

    def _patch(self, path: str, **kwargs):
        return self.session.patch(self._abs_url(path), **self.process_kwargs(**kwargs))

    def _patch_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('PATCH', self._patch, path, **kwargs)

    def _delete_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('DELETE', self._delete, path, **kwargs)

    def _retry_operation_name(self, method: str, path: str) -> str:
        return f'{self.__class__.__name__} {method} {self._abs_url(path)}'
