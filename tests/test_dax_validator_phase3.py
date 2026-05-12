"""Phase 3 foundation tests for DAX conversion guards."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from powerbi_import.dax_validator import validate_dax_expression
from tableau_export.dax_converter import convert_tableau_formula_to_dax


class TestDaxValidatorPhase3(unittest.TestCase):
    def test_valid_dax_has_no_issues(self):
        issues = validate_dax_expression("SUM('Sales'[Amount])")
        self.assertEqual(issues, [])

    def test_unbalanced_brackets_detected(self):
        issues = validate_dax_expression("SUM('Sales'[Amount)")
        self.assertTrue(any('unmatched' in i or 'mismatched' in i for i in issues))

    def test_unterminated_quotes_detected(self):
        issues = validate_dax_expression("'Sales[Amount]")
        self.assertTrue(any('unterminated' in i for i in issues))

    def test_tableau_function_leak_detected(self):
        issues = validate_dax_expression("DATETRUNC('year', [Order Date])")
        self.assertTrue(any('Tableau function' in i for i in issues))

    def test_empty_expression(self):
        issues = validate_dax_expression('   ')
        self.assertIn('empty DAX expression', issues)


class TestConverterGuardMode(unittest.TestCase):
    def test_default_behavior_unchanged(self):
        result = convert_tableau_formula_to_dax("SUM([Sales])", table_name='Orders')
        self.assertIn('SUM', result)

    def test_validate_output_no_fallback(self):
        result = convert_tableau_formula_to_dax(
            "DATETRUNC('year', [OrderDate])",
            validate_output=True,
            fallback_on_invalid=False,
        )
        # Conversion should still return a DAX string in non-fallback mode.
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_validate_output_with_fallback(self):
        result = convert_tableau_formula_to_dax(
            "[Sales] + \"unterminated",  # intentionally malformed string literal
            column_name='WinSum',
            validate_output=True,
            fallback_on_invalid=True,
        )
        self.assertIn('TODO: DAX conversion validation failed', result)
        self.assertIn('BLANK()', result)


if __name__ == '__main__':
    unittest.main()
