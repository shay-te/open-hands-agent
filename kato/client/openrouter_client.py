from kato.client.retrying_client_base import RetryingClientBase
from kato.helpers.text_utils import normalized_text


class OpenRouterClient(RetryingClientBase):
    provider_name = 'openrouter'

    def __init__(
        self,
        base_url: str,
        token: str,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self) -> None:
        response = self._get_with_retry('/models')
        response.raise_for_status()

    def validate_model_available(self, model: str) -> None:
        response = self._get_with_retry('/models')
        response.raise_for_status()
        available_models = self._available_model_ids(response.json())
        normalized_model = self._normalized_model_name(model)
        if not normalized_model or normalized_model in available_models:
            return
        raise RuntimeError(f'OpenRouter model not available: {normalized_model}')

    @staticmethod
    def _available_model_ids(payload) -> set[str]:
        if not isinstance(payload, dict):
            return set()
        models = payload.get('data', [])
        if not isinstance(models, list):
            return set()
        identifiers: set[str] = set()
        for model in models:
            if not isinstance(model, dict):
                continue
            for key in ('id', 'name', 'slug', 'model'):
                value = normalized_text(model.get(key, ''))
                if value:
                    identifiers.add(value)
        return identifiers

    @staticmethod
    def _normalized_model_name(model: str) -> str:
        normalized_model = normalized_text(model)
        if normalized_model.startswith('openrouter/'):
            return normalized_model.removeprefix('openrouter/')
        return normalized_model
