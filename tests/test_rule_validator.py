import unittest

from rule_validator import validate_data_access_crud_rule


class RuleValidatorTests(unittest.TestCase):
    def test_data_access_classes_use_crud_data_access_for_create_and_update(self) -> None:
        self.assertEqual(validate_data_access_crud_rule(), [])
