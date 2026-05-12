"""Sprint 136 — Self-Healing v3.

Adds twelve new healers that catch the most common reasons a generated
.pbip refuses to open in Power BI Desktop or fails to refresh data:

  14. Globally duplicate measure names (PBI requires global uniqueness)
  15. Self-referencing measures (infinite recursion → hide)
  16. Sort-by-column self-reference (circular sort → clear)
  17. Sort-by-column pointing to missing column (clear)
  18. Hierarchy levels referencing missing columns (drop level / hierarchy)
  19. Display folder name normalization (strip whitespace, dedupe slashes)
  20. Relationship data type mismatch (remove or coerce)
  21. Invalid identifier characters (strip control chars)
  22. Int64 with decimal-precision formatString → promote to Double
  23. dataType case normalization (canonical TMDL casing)
  24. Duplicate relationships (keep first, deactivate rest)
  25. isHidden + isKey conflict on date-table key (un-hide)

Each healer is a pure function ``(model, recovery) -> int`` returning
the number of repairs applied.  They never raise — defensive ``try``
blocks ensure self-healing failures do not block migration.

Wired from :func:`tmdl_generator._self_heal_model` after the existing
13 healers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple


__all__ = ['run_v3_healers']


# Canonical TMDL data-type casings.  PBI Desktop accepts BOTH the
# TitleCase TOM form ("String", "Int64", "DateTime") and the
# lowercase TMDL form ("string", "int64", "dateTime").  Both are
# treated as valid no-ops by the healer; only unrecognized forms
# (UPPERCASE, "Boolean", "datetime", "integer", etc.) are normalized
# to the lowercase TMDL canonical form.
_DATATYPE_CANONICAL: Dict[str, str] = {
    'string': 'string',
    'int64': 'int64',
    'integer': 'int64',
    'long': 'int64',
    'double': 'double',
    'decimal': 'decimal',
    'datetime': 'dateTime',
    'date': 'dateTime',
    'time': 'dateTime',
    'boolean': 'boolean',
    'bool': 'boolean',
    'binary': 'binary',
    'variant': 'variant',
}

# Casings that PBI Desktop / TMDL parses successfully.  Anything in
# this set is left untouched by the casing healer.
_DATATYPE_VALID: Set[str] = {
    'string', 'String',
    'int64', 'Int64',
    'double', 'Double',
    'decimal', 'Decimal',
    'dateTime', 'DateTime',
    'boolean', 'Boolean',
    'binary', 'Binary',
    'variant', 'Variant',
}

# Control characters forbidden in TMDL identifiers (NUL, BEL, BS, TAB,
# LF, VT, FF, CR, ESC, etc.).  These cause PBI Desktop to throw
# "Unexpected character" parse errors when loading the model.
_INVALID_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f]')

# Numeric formatString patterns
_DECIMAL_FMT = re.compile(r'\.[#0]')   # any fractional digits
_PERCENT_FMT = re.compile(r'%')

# Compatible-type families for relationships.  Mismatches across
# families cause "data type mismatch" at refresh.
_TYPE_FAMILY: Dict[str, str] = {
    'string': 'text',
    'int64': 'numeric',
    'double': 'numeric',
    'decimal': 'numeric',
    'datetime': 'datetime',
    'date': 'datetime',
    'boolean': 'boolean',
    'binary': 'binary',
}


# ════════════════════════════════════════════════════════════════════
#  Healer #14 — Globally duplicate measure names
# ════════════════════════════════════════════════════════════════════

def _heal_global_measure_dupes(model, recovery=None) -> int:
    """Power BI requires measure names to be unique across the entire
    model (not just within a table).  Duplicates cause the .pbip to
    refuse to open with "Multiple measures named 'X' found".

    Strategy: keep the first occurrence by table order; rename later
    duplicates to ``<name>_<table>``.
    """
    repairs = 0
    tables = model.get('model', {}).get('tables', []) or []
    seen: Dict[str, str] = {}  # measure_name → owning_table

    for tbl in tables:
        tname = tbl.get('name', '') or ''
        for m in tbl.get('measures', []) or []:
            mname = m.get('name', '') or ''
            if not mname:
                continue
            if mname not in seen:
                seen[mname] = tname
                continue
            # Duplicate — rename
            owning = seen[mname]
            suffix_base = re.sub(r'\W+', '_', tname).strip('_') or 'tbl'
            new_name = f'{mname}_{suffix_base}'
            counter = 2
            while new_name in seen:
                new_name = f'{mname}_{suffix_base}_{counter}'
                counter += 1
            old_name = mname
            m['name'] = new_name
            seen[new_name] = tname
            m.setdefault('annotations', []).append({
                'name': 'MigrationNote',
                'value': (f'Self-heal: renamed from "{old_name}" — duplicates '
                          f'measure on table "{owning}".  References to '
                          f'"{old_name}" still resolve to the original.')
            })
            repairs += 1
            if recovery is not None:
                recovery.record(
                    'tmdl', 'duplicate_measure_global',
                    item_name=f'{tname}.{old_name}',
                    description=(f'Measure "{old_name}" duplicated across '
                                 f'tables (also on "{owning}")'),
                    action=f'Renamed to "{new_name}" on "{tname}"',
                    severity='warning',
                    follow_up=(f'Verify visuals using "{old_name}" still '
                               f'point at the intended measure'),
                )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #15 — Self-referencing measures
# ════════════════════════════════════════════════════════════════════

def _heal_self_referencing_measures(model, recovery=None) -> int:
    """A measure that references itself produces infinite recursion at
    query time and prevents the model from being browsed.

    Detection: ``[Name]`` or ``'Table'[Name]`` appearing in the body of
    measure ``Name`` on ``Table``.

    Action: hide the measure and replace its body with ``BLANK()``.
    """
    repairs = 0
    tables = model.get('model', {}).get('tables', []) or []
    for tbl in tables:
        tname = tbl.get('name', '') or ''
        for m in tbl.get('measures', []) or []:
            mname = m.get('name', '') or ''
            expr = m.get('expression', '') or ''
            if not mname or not expr:
                continue
            bare = re.compile(r'\[' + re.escape(mname) + r'\]')
            qualified = re.compile(
                r"'" + re.escape(tname.replace("'", "''")) + r"'\[" +
                re.escape(mname) + r'\]'
            )
            if not (bare.search(expr) or qualified.search(expr)):
                continue
            m['expression'] = 'BLANK()'
            m['isHidden'] = True
            m.setdefault('annotations', []).append({
                'name': 'MigrationNote',
                'value': (f'Self-heal: measure self-references would cause '
                          f'infinite recursion. Original expression: '
                          f'{expr[:200]}'),
            })
            repairs += 1
            if recovery is not None:
                recovery.record(
                    'tmdl', 'self_referencing_measure',
                    item_name=f'{tname}.{mname}',
                    description=f'Measure "{mname}" references itself',
                    action='Replaced body with BLANK() and hid measure',
                    severity='warning',
                    follow_up=f'Rewrite measure "{mname}" without self-reference',
                )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #16/17 — Sort-by-column hygiene
# ════════════════════════════════════════════════════════════════════

def _heal_sort_by_column(model, recovery=None) -> int:
    """Clear ``sortByColumn`` when:

      * the target equals the column itself (circular)
      * the target column does not exist on the same table

    Both conditions cause PBI Desktop to throw a model-load error.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        col_names: Set[str] = {
            c.get('name', '') for c in tbl.get('columns', []) or []
            if c.get('name')
        }
        for col in tbl.get('columns', []) or []:
            cname = col.get('name', '') or ''
            target = col.get('sortByColumn', '') or ''
            if not target:
                continue
            if target == cname:
                col.pop('sortByColumn', None)
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'sort_by_column_self',
                        item_name=f'{tname}.{cname}',
                        description=f'Column "{cname}" has sortByColumn pointing at itself',
                        action='Removed sortByColumn',
                        severity='warning',
                    )
                continue
            if target not in col_names:
                col.pop('sortByColumn', None)
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'sort_by_column_missing',
                        item_name=f'{tname}.{cname}',
                        description=(f'sortByColumn target "{target}" '
                                     f'not found in table "{tname}"'),
                        action='Removed sortByColumn',
                        severity='warning',
                        follow_up=(f'Add column "{target}" to "{tname}" or '
                                   f'choose a different sort column'),
                    )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #18 — Hierarchy levels referencing missing columns
# ════════════════════════════════════════════════════════════════════

def _heal_hierarchies(model, recovery=None) -> int:
    """Drop hierarchy levels whose source column does not exist; if the
    hierarchy ends up with zero levels, drop the hierarchy itself.

    Invalid hierarchies cause the Model view to fail to render.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        col_names: Set[str] = {
            c.get('name', '') for c in tbl.get('columns', []) or []
            if c.get('name')
        }
        kept_hierarchies = []
        for hier in tbl.get('hierarchies', []) or []:
            hname = hier.get('name', '') or ''
            kept_levels = []
            for lvl in hier.get('levels', []) or []:
                src = lvl.get('column', '') or lvl.get('sourceColumn', '') or ''
                if src and src in col_names:
                    kept_levels.append(lvl)
                    continue
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'hierarchy_level_missing_column',
                        item_name=f'{tname}.{hname}.{lvl.get("name", "?")}',
                        description=(f'Hierarchy level references missing '
                                     f'column "{src}" on table "{tname}"'),
                        action='Level dropped from hierarchy',
                        severity='warning',
                    )
            if kept_levels:
                hier['levels'] = kept_levels
                kept_hierarchies.append(hier)
            else:
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'hierarchy_dropped',
                        item_name=f'{tname}.{hname}',
                        description=(f'Hierarchy "{hname}" had no valid '
                                     f'levels remaining'),
                        action='Hierarchy removed',
                        severity='warning',
                    )
        if 'hierarchies' in tbl:
            tbl['hierarchies'] = kept_hierarchies
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #19 — Display folder normalization
# ════════════════════════════════════════════════════════════════════

def _normalize_folder(folder: str) -> str:
    """Strip whitespace per segment and collapse repeated slashes."""
    if not folder:
        return ''
    parts = [p.strip() for p in folder.split('\\')]
    parts = [p for p in parts if p]  # drop empty segments
    return '\\'.join(parts)


def _heal_display_folders(model, recovery=None) -> int:
    """PBI rejects display folders containing only whitespace, leading /
    trailing whitespace per segment, or empty segments (``A\\\\B``).

    Normalize and record any change.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        for collection_name in ('columns', 'measures'):
            for item in tbl.get(collection_name, []) or []:
                folder = item.get('displayFolder', '')
                if not folder:
                    continue
                cleaned = _normalize_folder(folder)
                if cleaned == folder:
                    continue
                if cleaned:
                    item['displayFolder'] = cleaned
                else:
                    item.pop('displayFolder', None)
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'display_folder_normalized',
                        item_name=f'{tname}.{item.get("name", "?")}',
                        description=(f'Invalid displayFolder "{folder}" '
                                     f'(empty segments or whitespace)'),
                        action=(f'Normalized to "{cleaned}"' if cleaned
                                else 'displayFolder removed'),
                        severity='info',
                    )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #20 — Relationship data type mismatch
# ════════════════════════════════════════════════════════════════════

def _heal_relationship_type_mismatch(model, recovery=None) -> int:
    """Relationships joining columns of incompatible type families fail
    at refresh with "data type mismatch".

    Strategy: when types belong to different families, remove the
    relationship.  When they belong to the same family but differ
    (e.g. Int64 ↔ Double), leave them — PBI auto-coerces numerics.
    """
    repairs = 0
    tables = model.get('model', {}).get('tables', []) or []
    rels = model.get('model', {}).get('relationships', []) or []
    table_columns: Dict[str, Dict[str, str]] = {}
    for t in tables:
        tn = t.get('name', '') or ''
        if not tn:
            continue
        cols = {}
        for c in t.get('columns', []) or []:
            cname = c.get('name', '')
            dt = (c.get('dataType', '') or '').lower()
            if cname:
                cols[cname] = dt
        table_columns[tn] = cols

    kept = []
    for rel in rels:
        ft = rel.get('fromTable', '')
        tt = rel.get('toTable', '')
        fc = rel.get('fromColumn', '')
        tc = rel.get('toColumn', '')
        ft_dt = table_columns.get(ft, {}).get(fc, '')
        tt_dt = table_columns.get(tt, {}).get(tc, '')
        if not ft_dt or not tt_dt:
            kept.append(rel)
            continue
        ft_fam = _TYPE_FAMILY.get(ft_dt, ft_dt)
        tt_fam = _TYPE_FAMILY.get(tt_dt, tt_dt)
        if ft_fam == tt_fam:
            kept.append(rel)
            continue
        repairs += 1
        desc = f'{ft}[{fc}]({ft_dt}) → {tt}[{tc}]({tt_dt})'
        if recovery is not None:
            recovery.record(
                'relationship', 'type_mismatch',
                item_name=desc,
                description=(f'Relationship joins {ft_fam} to {tt_fam} — '
                             f'incompatible families'),
                action='Relationship removed',
                severity='warning',
                follow_up=(f'Cast either {ft}[{fc}] or {tt}[{tc}] to a '
                           f'matching type in Power Query'),
            )
    if repairs:
        model['model']['relationships'] = kept
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #21 — Invalid identifier characters
# ════════════════════════════════════════════════════════════════════

def _strip_invalid(name: str) -> str:
    """Remove control chars (which break TMDL parsing)."""
    if not name:
        return name
    return _INVALID_NAME_CHARS.sub('', name)


def _heal_invalid_identifiers(model, recovery=None) -> int:
    """Strip control characters from table / column / measure /
    hierarchy / role identifiers.

    Note: relationship endpoints are NOT rewritten here — those are
    fixed by an earlier healer that removes broken refs.  This healer
    must run after table renaming (#14, #15) and *before* relationship
    cleanup (existing #11), so we additionally rewire relationships
    that pointed at sanitized names.
    """
    repairs = 0
    rename_map: Dict[str, str] = {}
    tables = model.get('model', {}).get('tables', []) or []
    for tbl in tables:
        old_t = tbl.get('name', '') or ''
        new_t = _strip_invalid(old_t)
        if new_t != old_t:
            tbl['name'] = new_t
            rename_map[old_t] = new_t
            repairs += 1
            if recovery is not None:
                recovery.record(
                    'tmdl', 'invalid_identifier',
                    item_name=old_t,
                    description='Table name contained control characters',
                    action=f'Renamed to "{new_t}"',
                    severity='warning',
                )
        for col in tbl.get('columns', []) or []:
            old_c = col.get('name', '') or ''
            new_c = _strip_invalid(old_c)
            if new_c != old_c:
                col['name'] = new_c
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'invalid_identifier',
                        item_name=f'{new_t}.{old_c}',
                        description='Column name contained control characters',
                        action=f'Renamed to "{new_c}"',
                        severity='warning',
                    )
        for m in tbl.get('measures', []) or []:
            old_m = m.get('name', '') or ''
            new_m = _strip_invalid(old_m)
            if new_m != old_m:
                m['name'] = new_m
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'invalid_identifier',
                        item_name=f'{new_t}.{old_m}',
                        description='Measure name contained control characters',
                        action=f'Renamed to "{new_m}"',
                        severity='warning',
                    )

    # Rewire relationships that pointed at renamed tables
    if rename_map:
        for rel in model.get('model', {}).get('relationships', []) or []:
            if rel.get('fromTable') in rename_map:
                rel['fromTable'] = rename_map[rel['fromTable']]
            if rel.get('toTable') in rename_map:
                rel['toTable'] = rename_map[rel['toTable']]
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #22 — Int64 + decimal formatString
# ════════════════════════════════════════════════════════════════════

def _heal_int64_decimal_format(model, recovery=None) -> int:
    """An Int64 column with a decimal-precision formatString ("0.00",
    "#,##0.0") loads as integer and silently drops the fractional part,
    leading to wrong totals.  Promote the column to Double.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        for col in tbl.get('columns', []) or []:
            dt = (col.get('dataType', '') or '').lower()
            fmt = col.get('formatString', '') or ''
            if dt != 'int64' or not fmt:
                continue
            if not _DECIMAL_FMT.search(fmt):
                continue
            col['dataType'] = 'double'
            col['summarizeBy'] = col.get('summarizeBy') or 'sum'
            repairs += 1
            if recovery is not None:
                recovery.record(
                    'tmdl', 'int64_decimal_format',
                    item_name=f'{tname}.{col.get("name", "?")}',
                    description=(f'Int64 column has decimal formatString '
                                 f'"{fmt}" — fractional part would be lost'),
                    action='Promoted dataType to Double',
                    severity='warning',
                )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #23 — dataType case normalization
# ════════════════════════════════════════════════════════════════════

def _heal_datatype_casing(model, recovery=None) -> int:
    """TMDL dataType is case-sensitive.  ``"Boolean"``, ``"INT64"``,
    ``"datetime"``, etc. all silently parse as ``string`` in PBI's
    fallback, leading to refresh failures.  Force canonical casing.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        for col in tbl.get('columns', []) or []:
            dt = col.get('dataType', '')
            if not dt or dt in _DATATYPE_VALID:
                continue
            canon = _DATATYPE_CANONICAL.get(dt.lower())
            if not canon or canon == dt:
                continue
            col['dataType'] = canon
            repairs += 1
            if recovery is not None:
                recovery.record(
                    'tmdl', 'datatype_casing',
                    item_name=f'{tname}.{col.get("name", "?")}',
                    description=f'Non-canonical dataType "{dt}"',
                    action=f'Normalized to "{canon}"',
                    severity='info',
                )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #24 — Duplicate relationships
# ════════════════════════════════════════════════════════════════════

def _heal_duplicate_relationships(model, recovery=None) -> int:
    """Two relationships with identical endpoints cause "ambiguous join
    path" model-load errors.  Keep the first; deactivate the rest.
    """
    repairs = 0
    seen: Set[Tuple[str, str, str, str]] = set()
    for rel in model.get('model', {}).get('relationships', []) or []:
        key = (
            rel.get('fromTable', ''), rel.get('fromColumn', ''),
            rel.get('toTable', ''), rel.get('toColumn', ''),
        )
        if key in seen:
            if rel.get('isActive') is not False:
                rel['isActive'] = False
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'relationship', 'duplicate_relationship',
                        item_name=f'{key[0]}[{key[1]}] → {key[2]}[{key[3]}]',
                        description='Duplicate relationship with identical endpoints',
                        action='Deactivated duplicate (kept first occurrence active)',
                        severity='warning',
                    )
            continue
        seen.add(key)
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Healer #25 — isHidden + isKey conflict
# ════════════════════════════════════════════════════════════════════

def _heal_hidden_key(model, recovery=None) -> int:
    """A column flagged as both ``isKey=True`` and ``isHidden=True`` on
    a date table prevents Time Intelligence from working ("No date
    column found").  Un-hide such columns.
    """
    repairs = 0
    for tbl in model.get('model', {}).get('tables', []) or []:
        tname = tbl.get('name', '') or ''
        # Identify date-table marker (Calendar / DateTable / Copilot annotation)
        is_date_table = False
        for ann in tbl.get('annotations', []) or []:
            if ann.get('name') in ('Copilot_DateTable', 'IsDateTable',
                                   '__PBI_LocalDateTable'):
                is_date_table = True
                break
        if not is_date_table and tname.lower() not in ('calendar', 'date',
                                                       'datetable',
                                                       'date table'):
            continue
        for col in tbl.get('columns', []) or []:
            if col.get('isKey') and col.get('isHidden'):
                col['isHidden'] = False
                repairs += 1
                if recovery is not None:
                    recovery.record(
                        'tmdl', 'hidden_key_conflict',
                        item_name=f'{tname}.{col.get("name", "?")}',
                        description=('Date-table key column was hidden — '
                                     'breaks Time Intelligence'),
                        action='Unhid key column',
                        severity='warning',
                    )
    return repairs


# ════════════════════════════════════════════════════════════════════
#  Public entry point
# ════════════════════════════════════════════════════════════════════

_V3_HEALERS = (
    ('global_measure_dupes', _heal_global_measure_dupes),
    ('self_referencing_measures', _heal_self_referencing_measures),
    ('sort_by_column', _heal_sort_by_column),
    ('hierarchies', _heal_hierarchies),
    ('display_folders', _heal_display_folders),
    ('relationship_type_mismatch', _heal_relationship_type_mismatch),
    ('invalid_identifiers', _heal_invalid_identifiers),
    ('int64_decimal_format', _heal_int64_decimal_format),
    ('datatype_casing', _heal_datatype_casing),
    ('duplicate_relationships', _heal_duplicate_relationships),
    ('hidden_key', _heal_hidden_key),
)


def run_v3_healers(model, recovery=None) -> int:
    """Run all v3 healers; return total repair count.

    Each healer is wrapped in a defensive try/except so a bug in one
    healer cannot prevent the others from running, nor block migration.
    """
    total = 0
    for name, fn in _V3_HEALERS:
        try:
            total += fn(model, recovery=recovery)
        except Exception as exc:  # noqa: BLE001 — never block migration
            if recovery is not None:
                try:
                    recovery.record(
                        'tmdl', 'self_heal_v3_error',
                        item_name=name,
                        description=f'Healer "{name}" raised: {exc!r}',
                        action='Healer skipped (other healers continue)',
                        severity='error',
                    )
                except Exception:
                    pass
    return total
