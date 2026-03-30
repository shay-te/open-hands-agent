import unittest

from openhands_agent.openhands_config_utils import (
    is_bedrock_model,
    resolved_openhands_base_url,
    resolved_openhands_llm_settings,
)
from utils import build_test_cfg


class OpenHandsConfigUtilsTests(unittest.TestCase):
    def test_is_bedrock_model_ignores_surrounding_whitespace(self) -> None:
        self.assertTrue(
            is_bedrock_model('  bedrock/anthropic.claude-3-sonnet-20240229-v1:0  ')
        )
        self.assertFalse(is_bedrock_model('openai/gpt-4o'))

    def test_resolved_openhands_values_use_main_settings_when_testing_disabled(self) -> None:
        cfg = build_test_cfg().openhands_agent.openhands

        self.assertEqual(
            resolved_openhands_base_url(cfg, testing=True),
            cfg.base_url,
        )
        self.assertEqual(
            resolved_openhands_llm_settings(cfg, testing=True),
            {
                'llm_model': cfg.llm_model,
                'llm_base_url': cfg.llm_base_url,
            },
        )

    def test_resolved_openhands_values_use_testing_settings_when_testing_enabled(self) -> None:
        cfg = build_test_cfg().openhands_agent.openhands
        cfg.testing_container_enabled = True
        cfg.testing_base_url = 'https://openhands-testing.example'
        cfg.testing_llm_model = 'openai/gpt-4o-mini'
        cfg.testing_llm_base_url = 'https://api.openai.com/v1'

        self.assertEqual(
            resolved_openhands_base_url(cfg, testing=True),
            'https://openhands-testing.example',
        )
        self.assertEqual(
            resolved_openhands_llm_settings(cfg, testing=True),
            {
                'llm_model': 'openai/gpt-4o-mini',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )
