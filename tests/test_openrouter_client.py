import unittest
from unittest.mock import patch

from kato.client.openrouter import OpenRouterClient
from utils import assert_client_headers_and_timeout, mock_response


class OpenRouterClientTests(unittest.TestCase):
    def test_validate_connection_checks_models_endpoint(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={'data': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/models')
        assert_client_headers_and_timeout(self, client, 'or-key', 30)

    def test_validate_model_available_accepts_listed_model(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(
            json_data={
                'data': [
                    {'id': 'openai/gpt-4o-mini'},
                    {'id': 'anthropic/claude-3.5-haiku'},
                ]
            }
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_model_available('openrouter/openai/gpt-4o-mini')

        mock_get.assert_called_once_with('/models')
        response.raise_for_status.assert_called_once_with()

    def test_validate_model_available_rejects_missing_model(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={'data': [{'id': 'openai/gpt-4o-mini'}]})

        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(
                RuntimeError,
                'OpenRouter model not available: anthropic/claude-3.5-haiku',
            ):
                client.validate_model_available('openrouter/anthropic/claude-3.5-haiku')


if __name__ == '__main__':
    unittest.main()
