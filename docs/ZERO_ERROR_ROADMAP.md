# Zero-Error Migration Roadmap

**Goal:** Reach the state where any `.twbx` we accept produces a `.pbip` that
opens cleanly in Power BI Desktop **on the first try**, with **zero manual
fixes** and **zero silent data loss**.

**Baseline (post Sprint 140 / v31.3.0):**
- 7,628 tests passing, 94.0 % coverage
- 51 self-healing healers (40 model + 11 report)
- Validator catches structural defects post-write
- No formal cross-platform equivalence testing in CI
- No automated rollback on critical defects

**North-star metric:** _Zero-Touch Open Rate_ = % of corpus workbooks that
open in PBI Desktop with **0 errors / 0 warnings / 0 missing visuals**.
Today: estimated **~70 %** on the bug-bash corpus. Target: **≥ 99 %** by end
of Sprint 150.

---

## Strategy

The 10 phases form a **defence-in-depth** stack — each phase catches errors
the previous one missed. Earlier phases prevent; later phases verify and
recover.

```
 INPUT  ──▶  Phase 1: Pre-flight reject
            Phase 2: Extraction guards
            Phase 3: Conversion guards
       ──▶  Phase 4: Self-Healing v3.5 (model)
            Phase 5: Self-Healing v3.6 (report)
       ──▶  Phase 6: Cross-artifact validator
            Phase 7: Schema validator (PBI 2025 spec)
       ──▶  Phase 8: Equivalence testing in CI
            Phase 9: Auto-rollback + recovery report
            Phase 10: Continuous feedback loop
 OUTPUT ──▶  .pbip  (zero-touch open)
```

---

## Phase 1 — Pre-flight Rejection (Sprint 141 / v31.4.0)

**Owner:** @assessor + @orchestrator
**Goal:** Refuse early when migration would certainly fail.

| Check | Action |
|-------|--------|
| Workbook is encrypted / password-protected | Hard fail with clear message |
| Tableau version > supported (currently 2024.3) | Warn + require `--force` |
| Datasource uses unsupported connector (e.g. Hive 0.x, Splunk legacy) | Mark connector RED, suggest replacement |
| Workbook has corrupt/truncated XML | Hard fail with line-number diagnostic |
| `.twbx` ZIP slip / nested traversal | Hard fail (already in `security_validator`) |
| Workbook references non-existent extracts | Warn + offer `--ignore-missing-extracts` |
| Workbook size > 500 MB or visual count > 1,000 | Warn + suggest `--shared-model` split |

**Module:** new `powerbi_import/preflight.py` returning `PreflightResult`
(blockers, warnings, advisories). Wired into `migrate.py` before extraction.

**Tests:** ~30 unit tests + 5 fixture workbooks for each blocker class.

---

## Phase 2 — Extraction Guards (Sprint 142 / v31.5.0)

**Owner:** @extractor
**Goal:** Make `tableau_export/*` raise no silent `KeyError`/`AttributeError`
on malformed input.

- Wrap every `xml.etree` lookup with safe `_get(elem, attr, default)` helper
- Centralise in `tableau_export/safe_xml.py` — already partially exists in
  `security_validator`, lift its safe-parse helpers
- Add `ExtractionWarning` enum (28 known issues catalogued)
- All 17 extractors return `(data, warnings)` tuple instead of raising
- New regression: `tests/fixtures/malformed/` with 50 broken `.twb` snippets

**Coverage target:** `extract_tableau_data.py` 94.9 % → **97 %**

---

## Phase 3 — Conversion Guards (Sprint 143 / v31.6.0)

**Owner:** @dax + @wiring
**Goal:** Every Tableau→DAX and Tableau→M conversion either returns a valid
expression or returns `None` + a categorised warning. **Never a syntactically
broken string.**

- Add `dax_validator.validate_expression(expr) -> List[Issue]` that uses a
  small DAX grammar (already shipped in `dax_optimizer.parser`) to assert
  bracket balance, function arity, and known function names
- Same for M via `m_validator.py` (already exists — lift to mandatory pass)
- Every `dax_converter.convert_*` call wrapped in
  `try / except / fallback to TODO measure with note`
- New `ConversionRecovery` event type for telemetry

**Tests:** ~60 new tests covering every conversion that today silently
emits `[Foo]` without a table prefix or unbalanced parens.

---

## Phase 4 — Self-Healing v3.5 (model-side, Sprint 144 / v31.7.0)

**Owner:** @semantic
**Goal:** +10 healers covering model issues we keep seeing in bug-bash.

| Healer | Catches |
|--------|---------|
| `dax_unbalanced_brackets` | `[Col]` count ≠ closing `]` |
| `dax_unknown_function` | calls to `MAKEPOINT`, `SCRIPT_*` (post-conversion) |
| `dax_circular_dependency` | measure A ↔ B references |
| `relationship_orphan_table` | table never referenced and not a fact |
| `relationship_self_loop` | from-table = to-table |
| `column_duplicate_name_case` | `Date` and `date` collide in PBI |
| `column_invalid_datatype` | datatype not in {string,int,double,decimal,boolean,dateTime,binary} |
| `partition_empty_m` | M expression is `""` or `null` |
| `parameter_default_out_of_domain` | default value not in allowable list |
| `rls_missing_principal` | RLS role with no `tablePermissions` entry |

Wired into `_V3_HEALERS` (becomes 50 model healers).

---

## Phase 5 — Self-Healing v3.6 (report-side, Sprint 145 / v31.8.0)

**Owner:** @visual
**Goal:** +10 PBIR healers extending `self_healing_report.py` (becomes 21).

| Healer | Catches |
|--------|---------|
| `visual_overlap_full` | two visuals 100 % overlapping → stagger by 32 px |
| `visual_filter_unknown_field` | filter references column not in model |
| `visual_query_unknown_measure` | query projects measure not in model |
| `slicer_targets_missing_field` | slicer column was renamed/removed |
| `bookmark_targets_missing_visual` | bookmark visual states reference deleted visual |
| `theme_dataColors_empty` | `RegisteredResources/*.json` has empty palette |
| `page_no_visuals` | empty page → drop or add placeholder textbox |
| `pagesmeta_duplicate_pageorder` | same page listed twice |
| `tooltip_page_oversized` | tooltip page > 480×320 → resize |
| `mobile_layout_orphan_visual` | mobile layout references deleted visual |

---

## Phase 6 — Cross-artifact Validator (Sprint 146 / v31.9.0)

**Owner:** @semantic + @visual + @reviewer
**Goal:** Today the validator runs on TMDL **or** PBIR; bridge them.

- New `powerbi_import/cross_validator.py`:
  - Every visual query field reference must exist in the semantic model
  - Every relationship must reference real columns
  - Every RLS table must exist
  - Every theme `dataColors` index must be < N for actual data points
- Generates `cross_validation_report.html` + `.json`
- Pipeline: post-self-healing, runs in `--strict` mode (CI default)
- Failure in strict mode → exit code 4, no artifacts shipped

---

## Phase 7 — PBI Desktop Schema Validator (Sprint 147 / v31.10.0)

**Owner:** @visual
**Goal:** Validate every JSON artifact against the **actual** PBI 2025 JSON
schema (not just our internal expectations).

- Pull canonical schemas from
  `https://developer.microsoft.com/json-schemas/fabric/item/report/...` at
  build time (cached in `powerbi_import/schemas/`)
- New `schema_validator.py` using stdlib `jsonschema`-style walker (or vendor
  a tiny implementation; we still want zero deps)
- Run on every `*.json` in `<Report>/definition/` after self-healing
- Schema mismatches → repair where possible, else report

---

## Phase 8 — Equivalence Testing in CI (Sprint 148 / v32.0.0)

**Owner:** @tester + @reviewer
**Goal:** Catch silent semantic drift between Tableau and PBI output.

`equivalence_tester.py` already exists; promote to first-class CI gate:

1. **Corpus**: 25 representative `.twbx` (small / medium / complex / RLS /
   LOD / Prep / SCRIPT_*) — checked in under `tests/fixtures/equivalence/`
2. **Per workbook**:
   - Migrate end-to-end
   - Run `validator.full_check()` → must pass
   - Run `equivalence_tester.compare_measures()` against snapshot of expected
     values from a reference Tableau extract dump
   - Render headless PBI screenshots via `tableauhyperapi` + a tiny pbi
     evaluator (DAX-only via Microsoft.AnalysisServices), compare against
     baseline images at SSIM ≥ 0.97
3. **CI gates**: any drift > tolerance fails the build
4. New nightly job `.github/workflows/equivalence.yml`

---

## Phase 9 — Auto-Rollback + Recovery Report (Sprint 149 / v32.1.0)

**Owner:** @orchestrator + @reviewer
**Goal:** When a critical defect survives all healers, **don't ship** — back
off and emit a triage package.

- Severity ladder:
  - INFO → log only
  - WARNING → record in `RecoveryReport`, ship anyway
  - ERROR → ship to `<output>/_FAILED/` with `triage.html`
  - CRITICAL → roll back, leave only `triage_package.zip` (input + extraction
    JSONs + partial output + logs + recovery report)
- New `migrate.py --strict` exit codes:
  - 0 = clean
  - 1 = warnings only
  - 2 = errors (triage package emitted)
  - 3 = critical (rollback)
- Triage package auto-attaches to GitHub issue template

---

## Phase 10 — Continuous Feedback Loop (Sprint 150 / v32.2.0)

**Owner:** @assessor + @deployer
**Goal:** Every real-world failure becomes a regression test within 24 h.

- Telemetry v3 (opt-in): when a user runs `migrate.py --report-issue`, ship
  the redacted triage package to `https://issues.tableautopowerbi.dev`
- A weekly bot:
  - Triages new packages
  - Auto-derives a minimal repro `.twbx`
  - Opens a PR adding it to `tests/fixtures/regressions/`
  - Tags the most likely owner agent
- Dashboard: `docs/zero_error_dashboard.html` — Zero-Touch Open Rate over
  time, top failure modes, healers' hit-rate, validator catch-rate

---

## Per-Phase Exit Criteria

Every sprint must:
1. Add ≥ 30 unit tests, ≥ 95 % coverage on new code
2. Add ≥ 1 fixture workbook exercising the bug class end-to-end
3. Update Zero-Touch Open Rate metric in `docs/zero_error_dashboard.html`
4. Pass full suite with **0 regressions**
5. Bump CHANGELOG and the `ZERO_ERROR_ROADMAP.md` progress table

---

## Progress Tracker

| Phase | Sprint | Version | Status | Zero-Touch % |
|-------|--------|---------|--------|--------------|
| 1 — Pre-flight | 141 | v31.4.0 | ✅ Shipped | _baseline ~70 %_ |
| 2 — Extraction guards | 142 | v31.5.0 | 🟡 In progress | — |
| 3 — Conversion guards | 143 | v31.6.0 | ✅ Shipped | — |
| 4 — Self-Healing v3.5 | 144 | v31.7.0 | ✅ Shipped | — |
| 5 — Self-Healing v3.6 | 145 | v31.8.0 | ✅ Shipped | — |
| 6 — Cross-artifact validator | 146 | v31.9.0 | ✅ Shipped | — |
| 7 — Schema validator | 147 | v31.10.0 | ✅ Shipped | — |
| 8 — Equivalence in CI | 148 | v32.0.0 | ⏸ Planned | — |
| 9 — Auto-rollback | 149 | v32.1.0 | ⏸ Planned | — |
| 10 — Feedback loop | 150 | v32.2.0 | ⏸ Planned | **Target ≥ 99 %** |

---

## Risks

- **Headless PBI rendering** (Phase 8) is hard — may need a small AS-engine
  Docker image. Fallback: skip screenshot SSIM, keep measure-value drift.
- **Schema drift** at Microsoft's end (Phase 7) — pin to `2.5.0` family,
  re-pin manually each PBI Desktop release.
- **Telemetry privacy** (Phase 10) — must redact every connection string,
  PAT, sample data row before shipping. `security_validator` already has
  the redaction primitives; lift them.
