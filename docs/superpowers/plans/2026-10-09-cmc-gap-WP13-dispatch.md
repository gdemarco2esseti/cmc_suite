# WP-13 `cmc_fin_analysis` - dispatch plan (odoo-dev-lead, 2026-10-09)

Worktree `custom/cmc_suite01/.worktrees/gap-manuali` (branch `feat/gap-manuali`). Odoo 19 (profile + `.claude/rules/odoo-syntax.md`).
Binding inputs: red tests `cmc_fin_analysis/tests/{common,test_fin_config,test_fin_engine,test_fin_security}.py`
(CONTRACT docstring in `tests/common.py` = source of truth, NOT restated here), AD-111/113/114/115/116/118
(`docs/superpowers/plans/2026-10-09-cmc-gap-architecture.md`), formulas `docs/analysis/gap-manuali-2026-10/03_fazzini.md`,
FIN-01..11 in `10_gap_register.md`, readers `cmc_account/models/account_move_line.py`.
No TS-NNN / RF-AREA-NNN exist for this programme: traceability = FIN-xx + AD-xxx.

DB / commands (no conf file; pass `-d` explicitly). cwd `/Volumes/WDEXSSD/2ST/05_DEV/odoo19`,
`PY=/opt/homebrew/Caskroom/miniconda/base/envs/odoo-19/bin/python`:
`$PY -B odoo/odoo-bin -d cmc_gap_wp13 --addons-path=odoo/addons,enterprise,custom/cmc_suite01/.worktrees/gap-manuali --http-port=8813 -u cmc_fin_analysis --test-enable --test-tags=/cmc_fin_analysis --stop-after-init --log-level=test`
Rule: two jobs never run odoo-bin on the same db at the same time (W2 parallel -> second db `cmc_gap_wp13b`, port 8814, created by the main session with `createdb -T cmc_gap_wp13`).

## 0. Decisions

Test-writer design choices - all CONFIRMED:
1. One BS scheme (`scheme_bs_functional`) holding financial + functional aggregates: one mapping, one namespace, identity COIN = PN + PFN checkable in one place (少).
2. English line/ratio codes: codes are identifiers (safe_eval namespace); Italian lives in `name` (translate) + `i18n/it.po`.
3. Cash-flow schemes formula-only over `prev_<code>`: exactly AD-116 "cash flow derives from BS deltas + P&L of the same run".
4. Prior-year undistributed result via `result_line`: matches FIN-01 (PN includes undistributed results) without CMC writing entries (AD-111 ownership).
5. Choices register = `cmc.fin.override` with required `reason`: overrides + choices register of AD-116 merged in one model (少).

Delta vs AD text (non-blocking, report to software-architect for an AD-116 wording fix): mapping criteria are
account / code prefix / account type / **cmc_behavior**; AD-116 says "tag". Account-tag criterion: DEFERRED (no test, no shipped rule needs it).

Uncovered items (AD-118 Must for WP-13 does not list them, Skip does not either):
| Item | Decision | How (contract) |
|---|---|---|
| `cmc.fin.input` unique (company, year, code) | IN, now (already in contract) | Model job A implements; test-writer adds 1 test |
| FIN-06 commercial-cycle what-if | IN, data-only | inputs `target_dso`, `target_dio_raw`, `target_dio_finished`, `target_dpo`; ratios (area liquidity, full_only): `whatif_cycle` = target_dso+target_dio_raw+target_dio_finished-target_dpo (days); `whatif_cash_release` = (dso-target_dso)*revenue/360+(dio_raw-target_dio_raw)*raw_consumption/360+(dio_finished-target_dio_finished)*cs_cogs/360-(dpo-target_dpo)*(purchases+services)/360 (amount) |
| FIN-09 liquidity dynamics table | IN, data-only | formula-only scheme `scheme_cf_liquidity` (cash_flow, seq after `scheme_cf_oic10`): `liq_generated` = cf_gross_self_financing+fin_income+extraordinary; `liq_abs_op_c` = taxes+(ccnop-prev_ccnop)-(op_liab_nc-prev_op_liab_nc); `liq_abs_op_nc` = cf_op_investment+((fin_assets_nc+fin_assets_c)-(prev_fin_assets_nc+prev_fin_assets_c)); `liq_abs_debt` = fin_charges-(fin_debt-prev_fin_debt); `liq_abs_equity` = net_income-(equity-prev_equity); `liq_residual` = liq_generated-liq_abs_op_c-liq_abs_op_nc-liq_abs_debt-liq_abs_equity (= cf_cash_change); `liq_residual_pct` = liq_residual/liq_generated*100 |
| FIN-10 data-quality red flags | IN partial, data-only | new ratio area `data_quality` (Selection value added by Model); ratios threshold `<=` 0: `dq_intangible_growth` = intangible-prev_intangible; `dq_inventory_vs_revenue` = inventory/prev_inventory-revenue/prev_revenue; `dq_receivables_vs_revenue` = receivables/prev_receivables-revenue/prev_revenue. DEFERRED: participations above equity value, bad-debt fund vs ageing, impairment/fair value (need extra-ledger data: new AD) |
| FIN-11 MVA/EV | IN partial, data-only | ratios area value (amount): `mva_cov` = eva/wacc; `ev` = coin+mva_cov; `equity_value` = ev-pfn (CEIN = 0). DEFERRED: prospective EVA/FGV (needs forecast series, cmc_budget era); capital charge per product/customer -> WP-09 `cmc_profitability` (reads ratio `wacc`). Ask architect to record both |
| FIN-05 distortion flags (valuation-criteria change) | DEFERRED -> CONFLICT to architect | needs a new record type (event per company-year with impact on COIN/PFN/PN) + UI marker: not in AD-118 Must, adds a model. Until an AD decides, FIN-05 stays PARTIAL in the register |

The IN items need red tests first: request to `odoo-test-writer` (W1, parallel with Model job A), file
`cmc_fin_analysis/tests/test_fin_extras.py` (reuse `FinAnalysisCommon`; test-writer pins exact expected values and may adjust
formulas above - its version wins): (a) input duplicate -> IntegrityError; (b) shipped xmlid `scheme_cf_liquidity` formula-only,
area `data_quality` ratios with thresholds; (c) 2026 values of liq_* (+ liq_residual = cf_cash_change), whatif_* with target inputs,
dq_* statuses, mva_cov/ev/equity_value; (d) `cmc_fin_analysis/tests/test_fin_report.py`: `_fin_report_data()` shape (section 1) and
`_render_qweb_html` of `cmc_fin_analysis.action_report_cmc_fin_analysis` on a computed run contains "total_assets" row label and year headers.

## 1. Test classification

| Test | Layer | Notes |
|---|---|---|
| test_fin_config (all) | Model | shipped data + validations |
| test_fin_engine (all) | Model | engine, lifecycle, query count |
| test_fin_security (all) | Model | ACL, ir.rule, reader gate |
| test_fin_extras (to write) | Model | data-only additions + input index |
| test_fin_report (to write) | Model (`_fin_report_data`) + View (report template/action) | View depends on Model method |
Control: NOT involved (history/benchmark import = standard `load()` / import UI; no HTTP, no API). No OWL (`static/` empty).

## 2. Integration contract (additions to tests/common.py CONTRACT - nobody renames without dev-lead)

Models/fields/ACL/rules/shipped xmlids: exactly `tests/common.py` CONTRACT. Additions needed by View (Model writes, View reads):
- `cmc.fin.analysis.value`: `name` related `line_id.name`; `sequence` Integer (snapshot of line sequence at compute);
  `_order` = scheme sequence, line sequence, year. Aggregators: `value` sum, `common_size_pct`/`yoy_pct` avg, `year` aggregator False (pivot column, never summed).
- `cmc.fin.ratio.value`: `name`, `area` (stored), `unit`, `threshold_op`, `threshold` related to `ratio_id`; `value` aggregator avg; `year` aggregator False.
- `year` aggregator False also on history / input / benchmark / override.
- `cmc.fin.ratio.area` + value `data_quality` (label "Data quality").
- Method `cmc.fin.analysis._fin_report_data(self) -> dict` (ensure_one; called by View report template):
  `{"years": [int asc], "schemes": [{"scheme": rec, "lines": [{"code", "name", "line_type", "values": {year: float}, "common_size": {year: float}}]}],
    "ratios": [{"area": key, "area_label": str, "rows": [{"code", "name", "unit", "values": {year: {"value", "status", "benchmark" (float|None)}}}]}]}`
  missing value row for a year = key absent (template prints "n.c."). Built from the run's own result rows in one read each: zero queries per line/row.
- Run lifecycle methods (from `cmc.run.mixin`): `action_compute`, `action_reset_to_draft`, field `state` (draft/done). No new action method: drill-down
  to values/ratios = View `act_window` with context filter on `run_id`.
- No `mail.thread` on any model (no chatter).
Views/actions (View xmlids, referenced by menus/report only):
`action_cmc_fin_analysis` (list,form) · `action_cmc_fin_value` (pivot,graph,list; default pivot rows=scheme,line cols=year) ·
`action_cmc_fin_ratio_value` (list grouped by area,pivot) · `action_cmc_fin_crisis` (ratio values, domain area in crisis,data_quality, decoration by status) ·
`action_cmc_fin_scheme`, `action_cmc_fin_mapping_rule`, `action_cmc_fin_ratio`, `action_cmc_fin_override`, `action_cmc_fin_history`, `action_cmc_fin_input`, `action_cmc_fin_benchmark` ·
report `action_report_cmc_fin_analysis` (qweb-pdf, binding `cmc.fin.analysis`), template `report_cmc_fin_analysis_document` ·
views `view_cmc_fin_<model>_{list,form,search,pivot,graph}` as needed.
Menus: section `menu_cmc_sec_fin_analysis` "Financial Statement Analysis" (it: "Analisi di bilancio") parent `cmc_base.menu_cmc_operations` seq 30:
Analyses, Values (pivot), Ratios, Crisis Panel, Choices Register (override), Historical Balances, Reference Inputs, Sector Benchmarks.
Config `menu_cmc_cfg_fin_analysis` parent `cmc_base.menu_cmc_config` (manager): Schemes, Mapping Rules, Ratios.
Security: groups only `cmc_base.group_cmc_user` / `group_cmc_manager` (AD-115). View must not show create/edit on result models (ACL r only) and
hides config edit for users (ACL 1000) via `groups=` on menus; run inputs `readonly="state == 'done'"`.
Manifest delta (applied by Model only):
- depends `["cmc_account"]` (unchanged; AD-102). `cmc_suite/__manifest__.py` depends += `cmc_fin_analysis` (AD-102: suite installs every CE module).
- data order: `security/cmc_fin_security.xml` (rules), `security/ir.model.access.csv`, `data/cmc_fin_scheme_data.xml`, `data/cmc_fin_ratio_data.xml`,
  then (W3, after View) `views/cmc_fin_scheme_views.xml`, `views/cmc_fin_ratio_views.xml`, `views/cmc_fin_data_views.xml`,
  `views/cmc_fin_analysis_views.xml`, `report/cmc_fin_analysis_report.xml`, `views/cmc_fin_menus.xml` (last).
- Shipped data `noupdate="1"` for schemes/lines/rules/ratios (user-tunable rows) EXCEPT ir.rule (AD-115: not noupdate).

Efficiency / cardinality constraints (AD-114, closed here, not in QA):
- `account.move.line` unbounded: read ONLY via `_cmc_gl_balances_at` (aggregated, 2 queries per GL year) as the calling user (reader gate = AD-115;
  never sudo before the reader). No extra GL read for `result_line`: all needed figures are derivable from that reader's output (balanced ledger).
  Test `test_compute_query_count_independent_of_journal_items` is the gate.
- History: one grouped read for all history years of the span; GL is not read for a history year.
- Account -> line mapping: resolved once per run per scheme in memory (accounts with non-zero balance only; classification rules bounded master data); zero queries per account.
- Formulas/ratios: `safe_eval` over a dict namespace, nothing else (AD-116). Results: one batched `create(vals_list)` per result model, sudo under `CMC_RUN_STATE` with `# why`.
- `_fin_report_data`: bounded by lines x years of one run, built from prefetched rows; no `search` per line.
- Views: no non-stored computed column in lists; value pivot uses stored fields only.

## 3. File partition + sequence

| Layer | Writes | Never touches |
|---|---|---|
| Model (`odoo-model-dev`) | `cmc_fin_analysis/{__init__.py,__manifest__.py,models/**,security/**,data/**,i18n/**}`, `cmc_suite/__manifest__.py` | views/, report/, static/, controllers/, tests/ |
| View (`odoo-view-dev`) | `cmc_fin_analysis/views/**`, `cmc_fin_analysis/report/**` | models/, security/, data/, manifests, tests/ |
| Test-writer | `cmc_fin_analysis/tests/test_fin_extras.py`, `tests/test_fin_report.py`, `tests/__init__.py` | everything else |
| Control | - not dispatched - | |

Sequence:
- W1 parallel: Model job A (3 red files green) || test-writer extras (writes red tests only; does not run on db while A runs, or uses `cmc_gap_wp13b`).
- W2 parallel (after A green): Model job B (extras data + `_fin_report_data` + `data_quality`; db `cmc_gap_wp13b`, port 8814) || View job (views/report on `cmc_gap_wp13`).
  View validates its XML by temporarily loading files on its db? It cannot edit the manifest: it returns the file list; the main session runs W3 before View's own install check, OR View checks with `odoo-bin shell` / xmllint only. Recommended: W3 right after View returns.
- W3 serial: Model job C = manifest `data[]` += View files (list above), `i18n` export it.po (skill `odoo-i18n`, Italian labels incl. "Analisi di bilancio"), full module test run.
- Then `odoo-qa` (full regression: cmc suite 390 + cmc_fin_analysis) + `odoo-ux-tester` (smoke: compute run, pivot, crisis panel, PDF) -> ronin.

## 4. Dispatch blocks

### 4.1 odoo-model-dev - job A (W1)
RULE ZERO: `.claude/odoo-profile.md` + `.claude/rules/odoo-syntax.md` (Odoo 19: `models.Constraint`/`models.UniqueIndex`, `Domain`, `self.env._`, `group_ids`).
Worktree `/Volumes/WDEXSSD/2ST/05_DEV/odoo19/custom/cmc_suite01/.worktrees/gap-manuali`. db `cmc_gap_wp13`, port 8813, command above.
Goal: `test_fin_config`, `test_fin_engine`, `test_fin_security` green; implement the CONTRACT docstring of `cmc_fin_analysis/tests/common.py` verbatim
(models, validations, run on `cmc.run.mixin`/`cmc.run.result.mixin`, ACL table, global ir.rule `parent_of` not noupdate, shipped schemes/rules/ratios).
Also from this plan section 2: the extra fields on value/ratio value (name, sequence, area, unit, threshold related), aggregators, input UniqueIndex,
manifest data for security/data, `cmc_suite` depends += `cmc_fin_analysis`. Constraints: section 2 "Efficiency" verbatim.
Owns: `__init__.py`, `__manifest__.py`, `models/`, `security/`, `data/`, `cmc_suite/__manifest__.py`. Do NOT touch views/, report/, tests/.
Trace: FIN-01..FIN-11, AD-111/113/114/115/116. Report: files, test output tail, any contract ambiguity met.

### 4.2 odoo-test-writer - extras (W1)
Write red tests per section 0 "request to odoo-test-writer" (a)-(d), reusing `FinAnalysisCommon`; extend the CONTRACT docstring with the new
codes (`scheme_cf_liquidity`, `data_quality`, whatif/dq/value ratios, target_* inputs, `_fin_report_data` shape). Do not run odoo on `cmc_gap_wp13` while job A runs.

### 4.3 odoo-model-dev - job B (W2, after A green + extras written)
db `cmc_gap_wp13b`, port 8814. Goal: `test_fin_extras` green + `_fin_report_data` part of `test_fin_report` (report action part goes red until W3).
Data-only additions (scheme_cf_liquidity, ratios, area `data_quality`) + `_fin_report_data`. Same constraints, same ownership as job A. Trace FIN-05/06/09/10/11.

### 4.4 odoo-view-dev (W2, after A green)
RULE ZERO as above (`<list>`, inline `invisible`/`readonly`, no `attrs`, no chatter). db `cmc_gap_wp13`, port 8813.
Scope: files of section 3 View row with the xmlids/menus of section 2: config views (scheme form with inline lines `<list editable>` + rules tab;
mapping rule list; ratio list/form with threshold; override = "Choices Register" list with required reason; history/input/benchmark editable lists
importable via standard Import), run form (header Compute/Reset, inputs readonly when done, notebook: values list grouped by scheme with year,
ratios list grouped by area with status decoration, smart buttons to `action_cmc_fin_value` pivot/graph and ratio/crisis actions filtered on the run),
QWeb PDF `action_report_cmc_fin_analysis` = per scheme a table lines x years (+ common-size), ratios by area with status/benchmark, cash-flow schemes,
crisis panel - fed ONLY by `run._fin_report_data()`. No Python, no OWL. Results read-only (`create="0" edit="0"`).
Does NOT touch manifest: return the exact `data[]` list (must equal section 2) for Model job C. Trace FIN-01..11 (UI part of COVERED).

### 4.5 odoo-model-dev - job C (W3, serial after View + B)
Apply View `data[]` to `__manifest__.py` (order of section 2), export/fill `i18n/it.po`, run full module tests on `cmc_gap_wp13`. All 5 test files green.

## 5. Reconciliation checklist (dev-lead, on return)
grep shipped xmlids vs `SCHEME_XMLIDS`; ACL csv rows vs CONTRACT table; `ir.rule` records without noupdate; no `sudo()` before `_cmc_gl_balances_at`;
no `search(` inside per-line loops in engine; view xmlids/actions/menus vs section 2; manifest has one writer; `py_compile` on models.

## 6. Open to software-architect
1. FIN-05 distortion flags: new AD needed (proposed: per company-year valuation-change event with impact on COIN/PFN/PN, shown as flag on the run years) or accept FIN-05 PARTIAL.
2. FIN-11 prospective EVA (FGV/We from forecast) + capital charge per product/customer (-> WP-09): record as deferred/moved.
3. FIN-10 red flags needing extra-ledger data (participations vs equity, bad-debt fund vs ageing): deferred.
4. AD-116 wording: mapping criterion "tag" -> "cmc_behavior" (tag deferred).

## Orchestrator decision (2026-10-09) on FIN-05 distortion flags
In scope (goal: every gap COVERED). Minimal shape, no architect round needed: model `cmc.fin.criteria.change`
(company_id, year, name, reason, line_codes Char = comma-separated affected line codes). The run marks
`comparability_break=True` on value rows of those lines for that year and the YoY of the following comparison;
the report shows the flag. Tests by test-writer in `test_fin_extras.py`; Model job B implements it.
