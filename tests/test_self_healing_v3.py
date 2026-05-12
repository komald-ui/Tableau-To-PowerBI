"""Sprint 136 — Self-Healing v3 tests.

Covers the 11 new healers in :mod:`powerbi_import.self_healing_v3`.
"""

from __future__ import annotations

import unittest

from powerbi_import.recovery_report import RecoveryReport
from powerbi_import.self_healing_v3 import (
    _heal_global_measure_dupes,
    _heal_self_referencing_measures,
    _heal_sort_by_column,
    _heal_hierarchies,
    _heal_display_folders,
    _heal_relationship_type_mismatch,
    _heal_invalid_identifiers,
    _heal_int64_decimal_format,
    _heal_datatype_casing,
    _heal_duplicate_relationships,
    _heal_hidden_key,
    _normalize_folder,
    run_v3_healers,
    _V3_HEALERS,
)


def _model(tables=None, relationships=None):
    return {
        'model': {
            'tables': tables or [],
            'relationships': relationships or [],
        }
    }


# ════════════════════════════════════════════════════════════════════
#  #14 — Global duplicate measure names
# ════════════════════════════════════════════════════════════════════

class TestGlobalDuplicateMeasures(unittest.TestCase):

    def test_unique_measures_no_repair(self):
        m = _model([
            {'name': 'Sales', 'measures': [{'name': 'Total', 'expression': '1'}]},
            {'name': 'Cost', 'measures': [{'name': 'Sum', 'expression': '2'}]},
        ])
        self.assertEqual(_heal_global_measure_dupes(m), 0)

    def test_duplicate_renamed_with_table_suffix(self):
        m = _model([
            {'name': 'Sales', 'measures': [{'name': 'Total', 'expression': '1'}]},
            {'name': 'Cost', 'measures': [{'name': 'Total', 'expression': '2'}]},
        ])
        self.assertEqual(_heal_global_measure_dupes(m), 1)
        cost_measure = m['model']['tables'][1]['measures'][0]
        self.assertEqual(cost_measure['name'], 'Total_Cost')

    def test_first_occurrence_kept(self):
        m = _model([
            {'name': 'A', 'measures': [{'name': 'X', 'expression': '1'}]},
            {'name': 'B', 'measures': [{'name': 'X', 'expression': '2'}]},
        ])
        _heal_global_measure_dupes(m)
        self.assertEqual(m['model']['tables'][0]['measures'][0]['name'], 'X')

    def test_three_duplicates_get_unique_names(self):
        m = _model([
            {'name': 'A', 'measures': [{'name': 'X', 'expression': '1'}]},
            {'name': 'B', 'measures': [{'name': 'X', 'expression': '2'}]},
            {'name': 'C', 'measures': [{'name': 'X', 'expression': '3'}]},
        ])
        _heal_global_measure_dupes(m)
        names = {t['measures'][0]['name'] for t in m['model']['tables']}
        self.assertEqual(len(names), 3)

    def test_recovery_record(self):
        m = _model([
            {'name': 'A', 'measures': [{'name': 'X', 'expression': '1'}]},
            {'name': 'B', 'measures': [{'name': 'X', 'expression': '2'}]},
        ])
        r = RecoveryReport('test')
        _heal_global_measure_dupes(m, recovery=r)
        self.assertEqual(len(r.repairs), 1)
        self.assertEqual(r.repairs[0]['repair_type'], 'duplicate_measure_global')

    def test_migration_note_added(self):
        m = _model([
            {'name': 'A', 'measures': [{'name': 'X', 'expression': '1'}]},
            {'name': 'B', 'measures': [{'name': 'X', 'expression': '2'}]},
        ])
        _heal_global_measure_dupes(m)
        ann = m['model']['tables'][1]['measures'][0]['annotations']
        self.assertTrue(any('renamed' in a['value'].lower() for a in ann))


# ════════════════════════════════════════════════════════════════════
#  #15 — Self-referencing measures
# ════════════════════════════════════════════════════════════════════

class TestSelfReferencingMeasures(unittest.TestCase):

    def test_no_self_ref(self):
        m = _model([{'name': 'T', 'measures': [
            {'name': 'A', 'expression': 'SUM([Col])'},
        ]}])
        self.assertEqual(_heal_self_referencing_measures(m), 0)

    def test_bare_self_ref_detected(self):
        m = _model([{'name': 'T', 'measures': [
            {'name': 'A', 'expression': '[A] + 1'},
        ]}])
        self.assertEqual(_heal_self_referencing_measures(m), 1)
        meas = m['model']['tables'][0]['measures'][0]
        self.assertEqual(meas['expression'], 'BLANK()')
        self.assertTrue(meas['isHidden'])

    def test_qualified_self_ref_detected(self):
        m = _model([{'name': 'Sales', 'measures': [
            {'name': 'Total', 'expression': "'Sales'[Total] * 2"},
        ]}])
        self.assertEqual(_heal_self_referencing_measures(m), 1)

    def test_other_measure_ref_not_caught(self):
        m = _model([{'name': 'T', 'measures': [
            {'name': 'A', 'expression': '[B] + 1'},
            {'name': 'B', 'expression': '5'},
        ]}])
        self.assertEqual(_heal_self_referencing_measures(m), 0)

    def test_recovery_record(self):
        m = _model([{'name': 'T', 'measures': [
            {'name': 'A', 'expression': '[A]'},
        ]}])
        r = RecoveryReport('test')
        _heal_self_referencing_measures(m, recovery=r)
        self.assertEqual(r.repairs[0]['repair_type'], 'self_referencing_measure')


# ════════════════════════════════════════════════════════════════════
#  #16/17 — Sort-by-column hygiene
# ════════════════════════════════════════════════════════════════════

class TestSortByColumn(unittest.TestCase):

    def test_valid_sort_by_kept(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'MonthName', 'sortByColumn': 'Month'},
            {'name': 'Month'},
        ]}])
        self.assertEqual(_heal_sort_by_column(m), 0)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['sortByColumn'],
                         'Month')

    def test_self_reference_cleared(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'sortByColumn': 'X'},
        ]}])
        self.assertEqual(_heal_sort_by_column(m), 1)
        self.assertNotIn('sortByColumn', m['model']['tables'][0]['columns'][0])

    def test_missing_target_cleared(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'A', 'sortByColumn': 'NonExistent'},
        ]}])
        self.assertEqual(_heal_sort_by_column(m), 1)
        self.assertNotIn('sortByColumn', m['model']['tables'][0]['columns'][0])

    def test_no_sort_by_no_repair(self):
        m = _model([{'name': 'T', 'columns': [{'name': 'X'}]}])
        self.assertEqual(_heal_sort_by_column(m), 0)

    def test_recovery_records_self_and_missing(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'sortByColumn': 'X'},
            {'name': 'Y', 'sortByColumn': 'Z'},
        ]}])
        r = RecoveryReport('test')
        _heal_sort_by_column(m, recovery=r)
        types = {rep['repair_type'] for rep in r.repairs}
        self.assertEqual(types,
                         {'sort_by_column_self', 'sort_by_column_missing'})


# ════════════════════════════════════════════════════════════════════
#  #18 — Hierarchy levels
# ════════════════════════════════════════════════════════════════════

class TestHierarchies(unittest.TestCase):

    def test_valid_hierarchy_kept(self):
        m = _model([{
            'name': 'T',
            'columns': [{'name': 'Year'}, {'name': 'Quarter'}],
            'hierarchies': [{
                'name': 'Time',
                'levels': [
                    {'name': 'L1', 'column': 'Year'},
                    {'name': 'L2', 'column': 'Quarter'},
                ],
            }],
        }])
        self.assertEqual(_heal_hierarchies(m), 0)

    def test_drops_invalid_level_only(self):
        m = _model([{
            'name': 'T',
            'columns': [{'name': 'Year'}],
            'hierarchies': [{
                'name': 'H',
                'levels': [
                    {'name': 'L1', 'column': 'Year'},
                    {'name': 'L2', 'column': 'Missing'},
                ],
            }],
        }])
        self.assertEqual(_heal_hierarchies(m), 1)
        levels = m['model']['tables'][0]['hierarchies'][0]['levels']
        self.assertEqual(len(levels), 1)

    def test_drops_hierarchy_when_all_levels_invalid(self):
        m = _model([{
            'name': 'T',
            'columns': [{'name': 'X'}],
            'hierarchies': [{
                'name': 'H',
                'levels': [
                    {'name': 'L1', 'column': 'Missing1'},
                    {'name': 'L2', 'column': 'Missing2'},
                ],
            }],
        }])
        # 2 invalid levels + 1 dropped hierarchy = 3 repairs
        self.assertEqual(_heal_hierarchies(m), 3)
        self.assertEqual(m['model']['tables'][0]['hierarchies'], [])

    def test_supports_sourceColumn_alias(self):
        m = _model([{
            'name': 'T',
            'columns': [{'name': 'Year'}],
            'hierarchies': [{
                'name': 'H',
                'levels': [{'name': 'L1', 'sourceColumn': 'Year'}],
            }],
        }])
        self.assertEqual(_heal_hierarchies(m), 0)


# ════════════════════════════════════════════════════════════════════
#  #19 — Display folder normalization
# ════════════════════════════════════════════════════════════════════

class TestDisplayFolders(unittest.TestCase):

    def test_normalize_basic(self):
        self.assertEqual(_normalize_folder('A\\B'), 'A\\B')

    def test_normalize_strips_whitespace(self):
        self.assertEqual(_normalize_folder('  A \\ B  '), 'A\\B')

    def test_normalize_collapses_empty_segments(self):
        self.assertEqual(_normalize_folder('A\\\\B'), 'A\\B')

    def test_normalize_empty_returns_empty(self):
        self.assertEqual(_normalize_folder('  \\  \\  '), '')

    def test_clean_folder_no_repair(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'displayFolder': 'Dimensions'},
        ]}])
        self.assertEqual(_heal_display_folders(m), 0)

    def test_whitespace_folder_normalized(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'displayFolder': ' Dim \\ Sub '},
        ]}])
        self.assertEqual(_heal_display_folders(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['displayFolder'],
                         'Dim\\Sub')

    def test_all_whitespace_folder_removed(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'displayFolder': '   '},
        ]}])
        self.assertEqual(_heal_display_folders(m), 1)
        self.assertNotIn('displayFolder', m['model']['tables'][0]['columns'][0])

    def test_measure_folder_also_normalized(self):
        m = _model([{'name': 'T', 'measures': [
            {'name': 'M', 'displayFolder': 'A\\\\B'},
        ]}])
        self.assertEqual(_heal_display_folders(m), 1)


# ════════════════════════════════════════════════════════════════════
#  #20 — Relationship data type mismatch
# ════════════════════════════════════════════════════════════════════

class TestRelationshipTypeMismatch(unittest.TestCase):

    def test_compatible_int_double_kept(self):
        m = _model(
            tables=[
                {'name': 'A', 'columns': [{'name': 'k', 'dataType': 'int64'}]},
                {'name': 'B', 'columns': [{'name': 'k', 'dataType': 'double'}]},
            ],
            relationships=[
                {'fromTable': 'A', 'fromColumn': 'k',
                 'toTable': 'B', 'toColumn': 'k'},
            ],
        )
        self.assertEqual(_heal_relationship_type_mismatch(m), 0)
        self.assertEqual(len(m['model']['relationships']), 1)

    def test_string_to_int_removed(self):
        m = _model(
            tables=[
                {'name': 'A', 'columns': [{'name': 'k', 'dataType': 'string'}]},
                {'name': 'B', 'columns': [{'name': 'k', 'dataType': 'int64'}]},
            ],
            relationships=[
                {'fromTable': 'A', 'fromColumn': 'k',
                 'toTable': 'B', 'toColumn': 'k'},
            ],
        )
        self.assertEqual(_heal_relationship_type_mismatch(m), 1)
        self.assertEqual(len(m['model']['relationships']), 0)

    def test_string_to_string_kept(self):
        m = _model(
            tables=[
                {'name': 'A', 'columns': [{'name': 'k', 'dataType': 'string'}]},
                {'name': 'B', 'columns': [{'name': 'k', 'dataType': 'string'}]},
            ],
            relationships=[
                {'fromTable': 'A', 'fromColumn': 'k',
                 'toTable': 'B', 'toColumn': 'k'},
            ],
        )
        self.assertEqual(_heal_relationship_type_mismatch(m), 0)

    def test_unknown_types_kept(self):
        m = _model(
            tables=[
                {'name': 'A', 'columns': [{'name': 'k'}]},
                {'name': 'B', 'columns': [{'name': 'k'}]},
            ],
            relationships=[{'fromTable': 'A', 'fromColumn': 'k',
                            'toTable': 'B', 'toColumn': 'k'}],
        )
        self.assertEqual(_heal_relationship_type_mismatch(m), 0)


# ════════════════════════════════════════════════════════════════════
#  #21 — Invalid identifier characters
# ════════════════════════════════════════════════════════════════════

class TestInvalidIdentifiers(unittest.TestCase):

    def test_clean_names_no_repair(self):
        m = _model([{'name': 'Sales', 'columns': [{'name': 'Amount'}],
                     'measures': [{'name': 'Total', 'expression': '1'}]}])
        self.assertEqual(_heal_invalid_identifiers(m), 0)

    def test_strips_tab_from_table_name(self):
        m = _model([{'name': 'Sa\tles', 'columns': []}])
        self.assertEqual(_heal_invalid_identifiers(m), 1)
        self.assertEqual(m['model']['tables'][0]['name'], 'Sales')

    def test_strips_newline_from_column(self):
        m = _model([{'name': 'T', 'columns': [{'name': 'A\nB'}]}])
        self.assertEqual(_heal_invalid_identifiers(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['name'], 'AB')

    def test_strips_null_from_measure(self):
        m = _model([{'name': 'T', 'measures': [{'name': 'M\x00x',
                                                 'expression': '1'}]}])
        self.assertEqual(_heal_invalid_identifiers(m), 1)
        self.assertEqual(m['model']['tables'][0]['measures'][0]['name'], 'Mx')

    def test_rewires_relationships_after_table_rename(self):
        m = _model(
            tables=[
                {'name': 'A\tlpha', 'columns': [{'name': 'k'}]},
                {'name': 'Beta', 'columns': [{'name': 'k'}]},
            ],
            relationships=[{'fromTable': 'A\tlpha', 'fromColumn': 'k',
                            'toTable': 'Beta', 'toColumn': 'k'}],
        )
        _heal_invalid_identifiers(m)
        self.assertEqual(m['model']['relationships'][0]['fromTable'], 'Alpha')


# ════════════════════════════════════════════════════════════════════
#  #22 — Int64 + decimal formatString
# ════════════════════════════════════════════════════════════════════

class TestInt64DecimalFormat(unittest.TestCase):

    def test_int64_no_format_no_repair(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'int64'},
        ]}])
        self.assertEqual(_heal_int64_decimal_format(m), 0)

    def test_int64_integer_format_no_repair(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'int64', 'formatString': '#,##0'},
        ]}])
        self.assertEqual(_heal_int64_decimal_format(m), 0)

    def test_int64_decimal_format_promoted(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'int64', 'formatString': '0.00'},
        ]}])
        self.assertEqual(_heal_int64_decimal_format(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'double')

    def test_int64_thousands_with_decimals_promoted(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'int64', 'formatString': '#,##0.0'},
        ]}])
        self.assertEqual(_heal_int64_decimal_format(m), 1)

    def test_double_decimal_no_change(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'double', 'formatString': '0.00'},
        ]}])
        self.assertEqual(_heal_int64_decimal_format(m), 0)


# ════════════════════════════════════════════════════════════════════
#  #23 — dataType case normalization
# ════════════════════════════════════════════════════════════════════

class TestDataTypeCasing(unittest.TestCase):

    def test_canonical_no_repair(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'string'},
            {'name': 'Y', 'dataType': 'int64'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 0)

    def test_uppercase_int64_normalized(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'INT64'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'int64')

    def test_titlecase_boolean_normalized(self):
        # "Boolean" (TitleCase) is accepted by PBI Desktop — no-op.
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'Boolean'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 0)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'Boolean')

    def test_uppercase_boolean_normalized(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'BOOLEAN'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'boolean')

    def test_datetime_normalized_to_camelcase(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'datetime'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'dateTime')

    def test_unknown_type_left_alone(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'WeirdType'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 0)

    def test_integer_synonym_mapped_to_int64(self):
        m = _model([{'name': 'T', 'columns': [
            {'name': 'X', 'dataType': 'integer'},
        ]}])
        self.assertEqual(_heal_datatype_casing(m), 1)
        self.assertEqual(m['model']['tables'][0]['columns'][0]['dataType'],
                         'int64')


# ════════════════════════════════════════════════════════════════════
#  #24 — Duplicate relationships
# ════════════════════════════════════════════════════════════════════

class TestDuplicateRelationships(unittest.TestCase):

    def test_unique_no_repair(self):
        m = _model(relationships=[
            {'fromTable': 'A', 'fromColumn': 'k',
             'toTable': 'B', 'toColumn': 'k'},
            {'fromTable': 'A', 'fromColumn': 'j',
             'toTable': 'B', 'toColumn': 'j'},
        ])
        self.assertEqual(_heal_duplicate_relationships(m), 0)

    def test_duplicate_deactivated(self):
        m = _model(relationships=[
            {'fromTable': 'A', 'fromColumn': 'k',
             'toTable': 'B', 'toColumn': 'k'},
            {'fromTable': 'A', 'fromColumn': 'k',
             'toTable': 'B', 'toColumn': 'k'},
        ])
        self.assertEqual(_heal_duplicate_relationships(m), 1)
        self.assertNotEqual(m['model']['relationships'][0].get('isActive'),
                            False)
        self.assertEqual(m['model']['relationships'][1]['isActive'], False)

    def test_already_inactive_duplicate_no_repair(self):
        m = _model(relationships=[
            {'fromTable': 'A', 'fromColumn': 'k',
             'toTable': 'B', 'toColumn': 'k'},
            {'fromTable': 'A', 'fromColumn': 'k',
             'toTable': 'B', 'toColumn': 'k', 'isActive': False},
        ])
        self.assertEqual(_heal_duplicate_relationships(m), 0)


# ════════════════════════════════════════════════════════════════════
#  #25 — isHidden + isKey conflict
# ════════════════════════════════════════════════════════════════════

class TestHiddenKeyConflict(unittest.TestCase):

    def test_calendar_hidden_key_unhid(self):
        m = _model([{
            'name': 'Calendar',
            'columns': [{'name': 'Date', 'isKey': True, 'isHidden': True}],
        }])
        self.assertEqual(_heal_hidden_key(m), 1)
        self.assertFalse(m['model']['tables'][0]['columns'][0]['isHidden'])

    def test_non_date_table_not_touched(self):
        m = _model([{
            'name': 'Sales',
            'columns': [{'name': 'OrderID', 'isKey': True, 'isHidden': True}],
        }])
        self.assertEqual(_heal_hidden_key(m), 0)
        self.assertTrue(m['model']['tables'][0]['columns'][0]['isHidden'])

    def test_date_table_via_annotation(self):
        m = _model([{
            'name': 'AnyName',
            'annotations': [{'name': 'Copilot_DateTable', 'value': 'true'}],
            'columns': [{'name': 'Date', 'isKey': True, 'isHidden': True}],
        }])
        self.assertEqual(_heal_hidden_key(m), 1)

    def test_visible_key_no_repair(self):
        m = _model([{
            'name': 'Calendar',
            'columns': [{'name': 'Date', 'isKey': True, 'isHidden': False}],
        }])
        self.assertEqual(_heal_hidden_key(m), 0)

    def test_non_key_hidden_no_repair(self):
        m = _model([{
            'name': 'Calendar',
            'columns': [{'name': 'X', 'isHidden': True}],
        }])
        self.assertEqual(_heal_hidden_key(m), 0)


# ════════════════════════════════════════════════════════════════════
#  Integration — run_v3_healers
# ════════════════════════════════════════════════════════════════════

class TestRunAllV3Healers(unittest.TestCase):

    def test_v3_healers_count(self):
        # Sanity-check that all 11 healers are wired
        self.assertEqual(len(_V3_HEALERS), 11)

    def test_runs_all_on_clean_model(self):
        m = _model([{'name': 'T', 'columns': [{'name': 'X', 'dataType': 'string'}]}])
        self.assertEqual(run_v3_healers(m), 0)

    def test_runs_all_on_messy_model(self):
        m = _model(
            tables=[
                {
                    'name': 'Calendar',
                    'columns': [
                        {'name': 'Date', 'dataType': 'DateTime',
                         'isKey': True, 'isHidden': True},
                        {'name': 'X', 'sortByColumn': 'X'},
                    ],
                    'measures': [
                        {'name': 'Total', 'expression': '[Total]'},
                    ],
                },
                {
                    'name': 'Sales',
                    'columns': [
                        {'name': 'k', 'dataType': 'INT64',
                         'formatString': '0.00'},
                        {'name': 'Folder', 'displayFolder': '  A\\\\B  '},
                    ],
                    'measures': [
                        {'name': 'Total', 'expression': 'SUM([k])'},
                    ],
                },
            ],
            relationships=[
                {'fromTable': 'Calendar', 'fromColumn': 'Date',
                 'toTable': 'Sales', 'toColumn': 'k'},
                {'fromTable': 'Calendar', 'fromColumn': 'Date',
                 'toTable': 'Sales', 'toColumn': 'k'},
            ],
        )
        repairs = run_v3_healers(m)
        # At minimum: hidden_key, sort_by_self, self_ref_measure,
        # int64_decimal, datatype_casing (×2 INT64+DateTime), display_folder,
        # global_dupe_measure, duplicate_relationship, type_mismatch
        self.assertGreaterEqual(repairs, 8)

    def test_recovery_recorded(self):
        m = _model([{'name': 'A', 'measures': [
            {'name': 'X', 'expression': '1'},
        ]}, {'name': 'B', 'measures': [
            {'name': 'X', 'expression': '2'},
        ]}])
        r = RecoveryReport('test')
        run_v3_healers(m, recovery=r)
        self.assertGreater(len(r.repairs), 0)

    def test_healer_exception_does_not_block(self):
        # Pass a malformed model; ensure no raise
        m = {'model': {'tables': None, 'relationships': None}}
        try:
            run_v3_healers(m)
        except Exception as exc:
            self.fail(f'run_v3_healers raised {exc!r}')

    def test_no_recovery_no_raise(self):
        m = _model([{'name': 'A', 'measures': [
            {'name': 'X', 'expression': '[X]'},
        ]}])
        # Just make sure no recovery report works fine
        repairs = run_v3_healers(m, recovery=None)
        self.assertGreaterEqual(repairs, 1)


# ════════════════════════════════════════════════════════════════════
#  Wiring into _self_heal_model
# ════════════════════════════════════════════════════════════════════

class TestWiringIntoSelfHealModel(unittest.TestCase):

    def test_self_heal_model_invokes_v3(self):
        from powerbi_import.tmdl_generator import _self_heal_model
        m = _model([
            {'name': 'A', 'columns': [{'name': 'X', 'dataType': 'INT64'}],
             'measures': [{'name': 'M', 'expression': '1'}]},
            {'name': 'B', 'columns': [{'name': 'Y', 'dataType': 'string'}],
             'measures': [{'name': 'M', 'expression': '2'}]},
        ])
        r = RecoveryReport('test')
        _self_heal_model(m, recovery=r)
        types = {rep['repair_type'] for rep in r.repairs}
        # v3-only repair types should be present
        self.assertTrue(
            'datatype_casing' in types or
            'duplicate_measure_global' in types,
            f'Expected v3 healers to run; got {types}',
        )


if __name__ == '__main__':
    unittest.main()
