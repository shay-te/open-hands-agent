import unittest
from unittest.mock import patch


from kato.kato_instance import KatoInstance
from utils import build_test_cfg


class KatoInstanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        KatoInstance._app_instance = None

    def test_get_raises_before_init(self) -> None:
        KatoInstance._app_instance = None

        with self.assertRaisesRegex(RuntimeError, 'KatoCoreLib is not initialized'):
            KatoInstance.get()

    def test_init_is_idempotent(self) -> None:
        cfg = build_test_cfg()
        with patch(
            'kato.kato_core_lib.AgentService.validate_connections'
        ):
            KatoInstance.init(cfg)
            first = KatoInstance.get()
            KatoInstance.init(cfg)
            second = KatoInstance.get()

        self.assertIs(first, second)
