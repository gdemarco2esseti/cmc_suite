# WP-01 - cmc_base v2 foundation: integration contract and dispatch (odoo-dev-lead, 2026-10-09)

Spec: `2026-10-09-cmc-gap-architecture.md` (AD-101..119; esp. AD-110, AD-113, AD-114, AD-115, AD-118 row WP-01, AD-119).
Gap rows: CEN-02, CEN-03, CLS-02, CLS-04, ALC-01, ALC-04, ALC-08, CAP-01, BUD-05, FIN-13, INT-05 (`10_gap_register.md`).
Red tests (binding, CONTRACT docstrings): `cmc_base/tests/test_wp01_*.py` (8 files), `cmc_account/tests/test_wp01_reconciliation.py`.
Worktree `custom/cmc_suite01/.worktrees/gap-manuali`, branch `feat/gap-manuali`, base HEAD `481a5da`. Odoo 19 (`.claude/odoo-profile.md`,
`.claude/rules/odoo-syntax.md`). No TS/RF ids exist for this programme: traceability = CAP id + AD id.

## 1. Test-writer choices: confirmations / corrections

| # | Choice | Verdict | Notes |
|---|---|---|---|
| C-1 | Elementless cost lines = NOT controllable | **Confirmed** | AD-109: decision flags live on the element; unclassified cost (CLS-05 queue) cannot be charged to a manager. Implemented once in `_cmc_cost_groups(controllable=)`. |
| C-2 | ROI on average monthly capital | **Confirmed, made precise** | Average over the capital rows whose month is in the span (missing months are NOT zero-filled). One row per (responsibility, month): `models.UniqueIndex` on `(responsibility_id, date)`. |
| C-3 | Neutral = GL expense items without analytic distribution | **Confirmed (operational proxy)** | Partial distribution stays in `unexplained` (test). Expense types verified on v19 source: `expense`, `expense_depreciation`, `expense_direct_cost` (`account/models/account_account.py:62`). Domain: `("analytic_distribution", "in", [False])` - NOT `= False`: `analytic.mixin._search_analytic_distribution` raises "Operation not supported" for `=` (only `in/not in` with False passes through, `analytic_mixin.py:84`). Dev verifies a cleared distribution is never stored as `{}`. |
| C-4 | Responsible-group rules on directly owned centers only | **Corrected** | AD-115 restricts "actuals to the centers of the responsibility centers the user owns", and `_cmc_responsibility_summary` uses center_ids + CMC descendants. A rule on `center_id` would hide from the owner of a Dept the rows booked on its Sub (pivot != summary). Rule on the report becomes `[("rollup_center_id.cmc_responsibility_ids.owner_id", "=", user.id)]`: the owner sees every row rolled up into his centers (own rows at depth 0, subtree rows at depth >= 1). All current assertions still pass. **Back to test-writer (doc-only, non-blocking)**: fix the docstring of `test_wp01_responsible_security.py` and add one assertion "owner of a parent center sees the child's rows at rollup = parent". Rules on `cmc.responsibility.center`/`.capital` stay on `owner_id` (direct). |
| C-5 | ALC-01 posts a `+amount` counter-line on the source (sum 0) | **Confirmed** | Consistent with AD-110: allocation lines are a relocation of ACTUAL cost (not plan figures), so invariant (1) "every analytic line is an actual" holds; AD-005/AD-112 sign kept. Three guards are part of the contract (sec. 2): (a) `cmc_account` auto-classification and classification gate skip lines with `cmc_allocation_id`; (b) `_cmc_responsibility_summary` never counts allocation lines as revenue; (c) a computed allocation cannot be deleted (reset first). |

Other findings (no test contradicts an ADR or the v19 API; the items below are for the main session):
- **G-1 (AD-118 gap, needs architect/main-session ack)**: AD-118 says range warnings are "used by CVR derive and WP-02". No red test covers the CVR wiring and CVR lives in `cmc_direct_costing` (in-place WP-10, AD-101), outside WP-01 modules. WP-01 delivers the API `cmc.cost.function._cmc_range_warning()`; proposed: wiring into CVR derive moves to WP-10 (WP-02 consumes it natively).
- **G-2 (G-DATA, AD-117)**: FIN-13 COAN reads lines, not centers. A move line distributed 100% on two root plans with separate keys yields two analytic lines -> COAN double counts -> negative `unexplained`. It is shown, never absorbed (correct behaviour), but measure "analytic lines per move line > 1 at 100% each" on the client DB at G-DATA.
- **G-3 (release note)**: when CMC centers live on the Project plan (`account_id`), the core project profitability panel will show the allocation lines (counter-line `+amount` on the source project). Accepted consequence of ALC-01; document it.
- **T-hier (dev note)**: `test_wp01_hierarchy` creates a new ROOT plan after install. The SQL view must be rebuilt whenever root-plan columns change, and `ir.model.fields` drops columns with `DROP COLUMN ... CASCADE` (`base/models/ir_model.py:878`), which would silently drop the view. See contract (report).

## 2. Integration contract (source of truth - no name/signature change without dev-lead)

### 2.1 Pure helpers (`cmc_base/models/cmc_utils.py`, stdlib only, AD-119) - Model writes, Model engines read
- `high_low(points) -> (K, v)`; extremes chosen on Q; `ValueError` if < 2 points or Qmax == Qmin.
- `ols_fit(points) -> (K, v, r2)`; `r2 = Sxy^2/(Sxx*Syy)`, 1.0 when Syy == 0; `ValueError` if < 2 points or all Q equal.

### 2.2 `account.analytic.account` (_inherit, cmc_base)
- `cmc_responsibility_ids` M2M `cmc.responsibility.center`, inverse of `center_ids`: relation `cmc_responsibility_center_account_rel`, `column1="center_id"`, `column2="responsibility_id"` (the two sides use the same table, columns swapped). Read by View (center form tags).
- `_cmc_center_descendants()` -> self + transitive `cmc_child_ids`; one search per tree LEVEL (bounded master data, AD-114(6)); never per node.
- `_cmc_cost_groups(date_from=False, date_to=False, behavior=False, by_element=False, company=False, controllable=None)` - new keyword only, existing callers untouched (cmc_closing, cmc_capacity, cmc_full_costing, cmc_direct_costing). `True` -> element.cmc_controllable True; `False` -> element empty OR not controllable. Filter in the domain, not in Python.
- `_cmc_cost_rollup(date_from, date_to, company=False) -> {account_id: cost}`: own + all descendants, each line counted once, net `-amount` (same scope as `_cmc_cost_groups`). Queries: f(depth, number of plans), not f(lines, breadth).
- `action_cmc_drilldown(date_from, date_to) -> dict` act_window on `account.analytic.line`, domain = OR over plan columns of the subtree ids + `cmc_excluded = False` + span. Runs as the user (AD-115: no sudo on drill-down).

### 2.3 `account.analytic.line` (_inherit, cmc_base)
- `cmc_allocation_id` M2o `cmc.allocation`, `index="btree_not_null"`, `ondelete="restrict"`, `readonly=True`, `copy=False`.
- Lines with `cmc_allocation_id`: create/write/unlink only under `CMC_RUN_STATE` context, **even for superuser** -> UserError otherwise (test `test_posted_lines_locked`).
- Capacity hook `_cmc_capacity_rows`: follows every non-excluded dated line (any behavior, incl. empty), not only fixed. Same query shape (no per-line query added).
- **cmc_account side** (`account_analytic_line.py`): `create` manual-marking, `_cmc_auto_classify` and `_cmc_classification_gate` ignore lines with `cmc_allocation_id` (system lines keep the allocation's classification).

### 2.4 Cost element / driver (cmc_base)
- `cmc.cost.element`: `cmc_controllable` Bool default True, `cmc_avoidable` Bool default False, `cmc_sunk` Bool default False, `cmc_commitment` Selection `committed`/`discretionary` (optional); `@api.constrains` sunk AND avoidable -> ValidationError.
- `cmc.cost.driver`: `allocation_criterion` Selection `cause_effect`/`benefit`/`fairness`/`ability_to_bear`; `rationale` Text; `assignment` Selection `traced`/`allocated`, required, default `allocated`.

### 2.5 `cmc.cost.function` + `cmc.cost.function.step` (new, cmc_base) - CLS-02
- Fields exactly as in the test docstring: `name` (required), `company_id`, `center_id`, `element_id`, `driver_id`, `behavior` (`fixed`/`variable`/`semi_variable` default/`step`), `fixed_amount`, `variable_rate`, `range_min`, `range_max`, `method` (`manual` default/`high_low`/`ols`), `r2` (readonly), `date_from`, `date_to`, `step_ids`. `currency_id` related company currency (for display).
- Step: `function_id` (required, cascade, index), `company_id` related stored, `qty_from`, `qty_to`, `amount`; overlap -> ValidationError on the function (constrains on `step_ids`).
- `_cmc_evaluate(quantity) -> float`; `_cmc_range_warning(quantity) -> str | False` (translated, `self.env._`).
- `action_estimate()`: `check_access("write")` first (AccessError for users), UserError for manual / < 2 usable months. Cost per month via `_cmc_cost_read_group` grouped `date:month` on the center's plan column (+ element when set); quantity via `cmc.measure.quantity._read_group` (`version_id = False`, driver, center, company) grouped `date:month`. Two grouped reads total.

### 2.6 Responsibility (new, cmc_base) - CEN-03, BUD-05
- `cmc.responsibility.center`: `name` req, `company_id` req default env.company, `responsibility_type` (`cost` default/`revenue`/`profit`/`investment`), `owner_id` M2o res.users req, `center_ids` M2M (rel above, `check_company=True`), `capital_ids` O2m, `active` Bool default True, `currency_id` related.
- `cmc.responsibility.capital` (`_inherit = ["cmc.month.mixin"]`): `responsibility_id` req cascade index, `company_id` related stored, `currency_id` related, `amount` Monetary; `models.UniqueIndex("(responsibility_id, date)")`.
- `_cmc_responsibility_summary(date_from, date_to) -> {resp.id: {"controllable_cost", "non_controllable_cost", "revenue", "result", "invested_capital", "roi"}}`: `check_access("read")` on self first (AccessError), then sudo (# why) inside each record's perimeter = union of `center_ids._cmc_center_descendants()` (set: a center mapped twice counts once). **Cost** = `-amount` of lines with `amount < 0` OR `cmc_allocation_id` set; **revenue** = `amount` of lines with `amount > 0` AND no `cmc_allocation_id` (C-5 guard b). Grouped reads only, batched over the recordset.
- `cmc.responsibility.summary` (TransientModel, UI of the controllable-cost/ROI report): `date_from`, `date_to` (required), `line_ids` -> `cmc.responsibility.summary.line` (Transient: `summary_id`, `responsibility_id`, `responsibility_type` related, `owner_id` related, `currency_id`, the 6 figures). `action_compute()` fills one line per responsibility center visible to the user (record rules do the filtering) with ONE `_cmc_responsibility_summary` call; returns the wizard action.

### 2.7 `cmc.center.cost.report` (new SQL view, cmc_base) - CEN-02
- `_auto = False`, `_description`, `_order = "date desc"`; fields `company_id`, `center_id`, `rollup_center_id`, `depth` (Integer), `element_id`, `controllable` (Boolean: element controllable; no element = False), `date` (first of month), `cost` (Monetary/Float `aggregator="sum"`, positive, net `-amount`), `currency_id` related.
- `init()`: UNION ALL over the ROOT plan columns of `account_analytic_line` (`account.analytic.plan` roots, `_column_name()`), `cmc_excluded = false`, aggregated per (company, column value, element, month) BEFORE joining a recursive CTE of CMC ancestors (`cmc_parent_id`). Same scope as `_cmc_cost_groups` so pivot = roll-up = closing.
- Rebuild hooks (column set changes): `account.analytic.plan._sync_plan_column(model)` (after super, when `model == "account.analytic.line"`) and `account.analytic.plan.unlink()` (after super) call `env["cmc.center.cost.report"].init()`. Owner: Model (cmc_base `models/`).
- `action_open_lines()` -> `center_id.action_cmc_drilldown(date, month_end(date))` (button in list, `groups="cmc_base.group_cmc_user"`).

### 2.8 `cmc.allocation` becomes a run (cmc_base) - ALC-01, ALC-08
- `_inherit = ["cmc.run.mixin"]`; new `date` (required, default today), `element_id` (check_company), `posted_line_ids` O2m `account.analytic.line`/`cmc_allocation_id`. `_cmc_input_fields = ("company_id", "source_account_id", "driver_id", "amount", "date", "element_id", "line_ids")`, `_cmc_result_fields = ("posted_line_ids",)`.
- `cmc.allocation.line` `_inherit = ["cmc.run.line.mixin"]`, `_cmc_run_field = "allocation_id"`. `allocated_amount` unchanged (largest remainder).
- `_cmc_compute()`: one batched `create(vals_list)` of lines in the allocation's company, plan-aware column of each center, `cmc_cost_element_id = element_id`, `date = date`, target `-allocated_amount`, source counter `+amount`; conservation check (sum 0) fail loud.
- `unlink()` of a computed allocation -> UserError (reset first) - C-5 guard c.
- `_cmc_allocated_share(company, date_from, date_to)` api.model -> `{"total_overhead", "allocated", "share", "warning"}` per docstring; grouped reads; gate `group_cmc_user` (AccessError).

### 2.9 Closing step order (cmc_base) - ALC-04
- `cmc.closing.run`: `step_order` Selection `auto` (default)/`service_share`/`manual`; `step_ids` O2m `cmc.closing.step`; both appended to `_cmc_input_fields`. Only `step_down` reads them; `auto` = current `_ordered_providers` (unchanged figures for existing runs).
- `cmc.closing.step` (`_inherit = ["cmc.run.line.mixin"]`, `_cmc_run_field = "run_id"`): `run_id` req cascade index, `sequence` Integer, `center_id` req, `company_id` related stored; `models.UniqueIndex("(run_id, center_id)")`. Manual: every to-close center with cost listed, and a listed center that is not to-close -> ValidationError at compute.

### 2.10 Capacity levels (cmc_base) - CAP-01
- `cmc.capacity.analysis`: inputs `capacity_normal`, `capacity_budget`, `downtime_rate`; `models.Constraint` CHECKs (>= 0; <= theoretical when theoretical > 0; downtime 0..100). `action_estimate_practical()`. Stored computes `total_cost` (all behaviors, one extra grouped read per period batch) and `coefficient_theoretical/practical/normal/budget/actual` (Float). Existing fixed-cost figures untouched.

### 2.11 Data checks (cmc_base) - INT-05 (+ ALC-08 UI)
- `cmc.data.check` AbstractModel: `_cmc_check_driver_volumes(company, date_from, date_to, tolerance=0.05)`, `_cmc_check_prior_period(company, date_from, date_to, threshold=0.5)` exactly per docstring; gate `group_cmc_user` (AccessError), aggregation in sudo inside the company; prior span via `cmc_utils.month_range`.
- `cmc.data.check.wizard` (Transient): `company_id`, `date_from`, `date_to`, `tolerance`, `threshold`, `line_ids` -> `cmc.data.check.wizard.line` (`wizard_id`, `check_type` Selection `driver_volume`/`prior_period`/`allocated_share`, `driver_id`, `center_id`, `value_current`, `value_reference`, `difference`, `change_pct`, `warning` Bool, `message` Char). `action_run()` = the two checks + `_cmc_allocated_share` (one line), returns the wizard action.

### 2.12 `cmc.coge.reconciliation` (new, cmc_account) - FIN-13
- Per docstring: `_inherit = ["cmc.run.mixin"]`, `name`, `company_id`, `date_from`, `date_to`, `currency_id`; results `coge_amount`, `coan_amount`, `figurative_amount`, `neutral_amount`, `unexplained_amount` (written only under sentinel, cleared by `_cmc_reset_values`; a write of them outside the sentinel -> UserError). `_cmc_input_fields = ("company_id", "date_from", "date_to")`; date order check.
- GL side reuses `account.move.line._cmc_reader_scope(company)` + `_cmc_posted_domain(...)` (no new reader); expense types constant next to `REVENUE_TYPES`; neutral per C-3. COAN via `_cmc_cost_read_group` in sudo (# why) on company lines `cmc_excluded = False`, excluding `general_account_id.account_type in REVENUE_TYPES`. Query count constant (~4 grouped reads).

### 2.13 Security (cmc_base `security/**`, cmc_account `security/**` - written by Model only)
- `cmc_base.group_cmc_responsible` "Controlling Responsible" (privilege `res_groups_privilege_cmc`, no implied group); `group_cmc_user.implied_ids` += responsible. Declared BEFORE `group_cmc_user` in `cmc_security.xml`; existing DBs get the implied link in the migration (the group block is `noupdate`).
- ACL (r,w,c,u): cost function + step: user 1000, manager 1111 · responsibility center + capital: responsible 1000, user 1000, manager 1111 · center cost report: responsible 1000, user 1000 · closing step: user 1111, manager 1111 · data check wizard + line: user 1111 · responsibility summary + line: responsible 1111 · `analytic.model_account_analytic_account`: responsible 1000 (center names in pivot; no line access: drill-down stays a user feature) · coge reconciliation (cmc_account): user 1110, manager 1111.
- Rules (NOT noupdate): global `company_id parent_of company_ids` on cost function, step, responsibility center, capital, center cost report, closing step, coge reconciliation. Responsible: center `[("owner_id", "=", user.id)]`, capital `[("responsibility_id.owner_id", "=", user.id)]`, report `[("rollup_center_id.cmc_responsibility_ids.owner_id", "=", user.id)]`. group_cmc_user: `[(1, "=", 1)]` on the same three.

### 2.14 Views / actions / menus (View owns; xmlids fixed here)
cmc_base `views/`:
- `cmc_cost_function_views.xml`: `view_cmc_cost_function_list|form|search`, `action_cmc_cost_function`, `menu_cmc_cost_function` (parent `menu_cmc_cfg_master`). Form: step_ids editable list `invisible="behavior != 'step'"`; `action_estimate` button `invisible="method == 'manual'"`; r2 readonly.
- `cmc_responsibility_views.xml`: `view_cmc_responsibility_center_list|form|search`, `action_cmc_responsibility_center`, `menu_cmc_responsibility_center`; `view_cmc_responsibility_summary_form` (+ line list inline), `action_cmc_responsibility_summary` (target new), `menu_cmc_responsibility_summary`. Both under new section `menu_cmc_responsibility` "Responsibility" (parent `menu_cmc_root`, seq 15, `groups="cmc_base.group_cmc_responsible"`).
- `cmc_center_cost_report_views.xml`: `view_cmc_center_cost_report_pivot|graph|list|search` (filters: Controllable, Booked here = depth 0; group by rollup center/center/element/month), `action_cmc_center_cost_report`, `menu_cmc_center_cost_report` (under `menu_cmc_responsibility`).
- `cmc_data_check_views.xml`: `view_cmc_data_check_wizard_form`, `action_cmc_data_check_wizard`, `menu_cmc_data_check` (parent `menu_cmc_sec_analysis`).
- Edits: `cmc_allocation_views.xml` (state badge, Compute/Reset buttons, date, element_id, posted lines tab readonly, inputs `readonly="state == 'done'"`); `cmc_closing_views.xml` (step_order + step_ids with `invisible="method != 'step_down'"` / `step_order != 'manual'`); `cmc_capacity_views.xml` (3 inputs, estimate button, total_cost, 5 coefficients); `cmc_cost_driver_views.xml` (3 fields); `cmc_reporting_views.xml` (element flags in cost element list/form); `account_analytic_account_views.xml` (`cmc_responsibility_ids` tags, readonly); `account_analytic_line_views.xml` (optional `cmc_allocation_id` column); `cmc_menus.xml` (root `groups` += responsible; `menu_cmc_operations`, `menu_cmc_dashboard` and `menu_cmc_cockpit` (in `cmc_cockpit_views.xml`) get `groups="cmc_base.group_cmc_user"`; new `menu_cmc_responsibility`). Note: menuitem `groups` links (Command.link), it does not replace.
cmc_account `views/`: `cmc_coge_reconciliation_views.xml`: `view_cmc_coge_reconciliation_list|form|search`, `action_cmc_coge_reconciliation`, `menu_cmc_coge_reconciliation` (parent `cmc_base.menu_cmc_sec_analysis`). Result figures readonly; Compute/Reset buttons.

### 2.15 Manifest delta (applied by Model only)
- cmc_base `19.0.1.3.0 -> 19.0.1.4.0`; `depends` unchanged. `data[]` (M-bis), after `views/cmc_cost_object_views.xml`: `views/cmc_cost_function_views.xml`, `views/cmc_responsibility_views.xml`, `views/cmc_center_cost_report_views.xml`, `views/cmc_data_check_views.xml`.
- cmc_account `19.0.1.4.0 -> 19.0.1.5.0`; `depends` unchanged. `data[]` (M-bis): `views/cmc_coge_reconciliation_views.xml` after `views/cmc_exception_views.xml`.
- Migration `cmc_base/migrations/19.0.1.4.0/post-migrate.py` (set-based, logs counts): (1) existing `cmc_allocation.date = create_date::date` (the ORM fills the new required column with today); existing allocations stay **draft** (they were never booked: no figure changes until a user computes them - release note); (2) `group_cmc_user.implied_ids` += `group_cmc_responsible` (noupdate block). New stored computes (capacity totals/coefficients), defaults (element flags, driver assignment, `step_order = auto`) are filled by the ORM: no script. cmc_account: no migration (new model only).
- i18n: `it.po` of both modules after green (skill `odoo-i18n`).

## 3. Test classification -> layer

| Test file | CAP / AD | Layer | Depends on |
|---|---|---|---|
| test_wp01_cost_function | CLS-02, AD-119 | M-base | - |
| test_wp01_hierarchy | CEN-02, AD-114 | M-base | - |
| test_wp01_responsibility | CEN-03, CLS-04, AD-115 | M-base | hierarchy (`_cmc_center_descendants`) |
| test_wp01_responsible_security | BUD-05, AD-115 | M-base (security) | responsibility, report |
| test_wp01_allocation_run | ALC-01, ALC-08, AD-113 | M-base | analytic line lock |
| test_wp01_step_down | ALC-04, AD-001 | M-base | - |
| test_wp01_capacity_levels | CAP-01 | M-base | capacity hook |
| test_wp01_data_checks | INT-05, AD-114 | M-base | - |
| test_wp01_reconciliation | FIN-13, AD-111/113 | M-acc | cmc_base unchanged parts only |
All tests are backend: View has no red test (UI verified by loading + `odoo-ux-tester` smoke). No Control layer (no controller/route): `odoo-controller-dev` NOT dispatched.

## 4. File partition and sequence

| Job | Writes | Never touches |
|---|---|---|
| M-base (`odoo-model-dev`) | `cmc_base/models/**`, `cmc_base/security/**`, `cmc_base/migrations/19.0.1.4.0/**`, `cmc_base/__manifest__.py` | `views/`, `static/`, `report/`, `tests/`, other modules |
| M-acc (`odoo-model-dev`) | `cmc_account/models/**`, `cmc_account/security/**`, `cmc_account/__manifest__.py` | `cmc_base/**`, `views/`, `tests/` |
| V (`odoo-view-dev`) | `cmc_base/views/**`, `cmc_account/views/**` | `models/`, `security/`, manifests, `tests/` |
| M-bis (`odoo-model-dev`) | both `__manifest__.py` `data[]` only | everything else |

Sequence (serial unless noted):
1. **M-base** alone (bulk; fixes the field names V and M-acc hook into).
2. **M-acc ∥ V-base** after M-base green. Can the two Model jobs run in parallel from the start? Files are disjoint and the only coupling is the field name `cmc_allocation_id`, but both share ONE worktree and ONE db: an M-acc test run would load a half-written cmc_base and concurrent `-u` on the same db conflicts. Hence M-base -> M-acc. V-base works on a db clone (`createdb -T cmc_gap_wp01 cmc_gap_wp01_v`) and validates its XML without the manifest, e.g. `odoo-bin shell` + `odoo.tools.convert.convert_file(...)` then rollback; the V agent writes each file in one go.
3. **V-acc** (reconciliation views) after M-acc green -> **M-bis** (data[] of both) -> full regression (`odoo-qa`) + `odoo-ux-tester` smoke (responsible-only user: sees Responsibility menu only) -> ronin gate.

## 5. Dispatch blocks (paste into the Agent call)

Common to all: RULE ZERO (`/Volumes/WDEXSSD/2ST/05_DEV/odoo19/.claude/odoo-profile.md`, `.claude/rules/odoo-syntax.md`, Odoo 19). Worktree `/Volumes/WDEXSSD/2ST/05_DEV/odoo19/custom/cmc_suite01/.worktrees/gap-manuali`. Contract = `docs/superpowers/plans/2026-10-09-cmc-gap-WP01-dispatch.md` sec. 2 (do not rename anything; ask dev-lead). `tests/**` read-only. No commit. Test command (cwd `/Volumes/WDEXSSD/2ST/05_DEV/odoo19`, `PY=/opt/homebrew/Caskroom/miniconda/base/envs/odoo-19/bin/python`):
```
$PY -B odoo/odoo-bin -d cmc_gap_wp01 --addons-path=odoo/addons,enterprise,custom/cmc_suite01/.worktrees/gap-manuali -u cmc_base,cmc_full_costing,cmc_direct_costing,cmc_account,cmc_hr_timesheet,cmc_mrp,cmc_abc,cmc_joint_costs,cmc_tdabc,cmc_suite --test-enable --test-tags=/cmc_base,/cmc_full_costing,/cmc_direct_costing,/cmc_account,/cmc_hr_timesheet,/cmc_mrp,/cmc_abc,/cmc_joint_costs,/cmc_tdabc,/cmc_suite --stop-after-init --log-level=test 2>&1 | grep -E "tests when loading|^(FAIL|ERROR):|Traceback"
```
(focused runs: `--test-tags=/cmc_base:TestCostFunction` etc.; `ERROR: ... viola il vincolo` lines are expected constraint tests). Config: none (explicit `--addons-path`), db `cmc_gap_wp01`.

### M-base - `odoo-model-dev` (cmc_base)
- Scope: sec. 2.1-2.11 + 2.13 (cmc_base part) + 2.15 (version bump, migration). CAP: CEN-02, CEN-03, CLS-02, CLS-04, ALC-01, ALC-04, ALC-08, CAP-01, BUD-05, INT-05. AD: 110, 113, 114, 115, 119.
- Green: all 8 `cmc_base/tests/test_wp01_*.py` + full suite regression (390 + A0 tests, none red).
- Constraints: unbounded tables (`account.analytic.line`, `cmc.measure.quantity`, `cmc.allocation`) read only with `_read_group`/`_cmc_cost_read_group`/SQL at the target grain; zero per-line queries (three query-count tests are binding); Python walks only over centers; batched `create(vals_list)`; sudo only for aggregates with `# why`; `Domain` objects; `models.Constraint`/`UniqueIndex`; `self.env._`; no new depends. The SQL view must survive plan creation/deletion (2.7 hooks). Do not change existing closing figures for `step_order = auto`.
- Must not touch: `views/`, `static/`, `report/`, `cmc_account/**`, tests. Report the manifest `data[]` you did NOT add (that is M-bis).

### M-acc - `odoo-model-dev` (cmc_account) - after M-base green
- Scope: sec. 2.12, 2.3 (cmc_account side: skip `cmc_allocation_id` lines in create manual-marking / `_cmc_auto_classify` / classification gate), 2.13 (cmc_account ACL + company rule), version 19.0.1.5.0. CAP: FIN-13. AD: 111, 113, 114, 115.
- Green: `cmc_account/tests/test_wp01_reconciliation.py` + `test_wp01_allocation_run` (cmc_base, now with cmc_account installed: posted lines must stay unlocked-free of classification writes) + full regression.
- Constraints: reuse `_cmc_reader_scope` + `_cmc_posted_domain` (no new GL reader); neutral domain per C-3; constant query count; results written only under sentinel.

### V - `odoo-view-dev` (cmc_base views now, cmc_account views after M-acc)
- Scope: sec. 2.14 (all xmlids fixed there). Odoo 19 syntax: `<list>`, inline `invisible`/`readonly`, no `attrs`/`states`; `groups=` on buttons that responsible users must not see (drill-down `action_open_lines`). Relabel nothing in selections. Cost function/responsibility/reconciliation figures readonly when computed.
- DB: clone `cmc_gap_wp01_v` (step 2); validate by loading each file (shell + `convert_file`, rollback). Deliver the list of files for `data[]` (M-bis applies it).
- Must not touch: `models/`, `security/`, manifests, tests.

### M-bis - `odoo-model-dev`
- Add the 4 + 1 view files to `data[]` (sec. 2.15) and run the full test command on `cmc_gap_wp01`.

## 6. Existing files edited (summary)
cmc_base: `models/cmc_utils.py`, `account_analytic_account.py`, `account_analytic_line.py`, `cmc_allocation.py`, `cmc_cost_driver.py`, `cmc_cost_analysis.py` (element flags), `cmc_closing.py`, `cmc_capacity.py`, `models/__init__.py`; `security/cmc_security.xml`, `security/ir.model.access.csv`; `__manifest__.py`; views `cmc_allocation_views.xml`, `cmc_closing_views.xml`, `cmc_capacity_views.xml`, `cmc_cost_driver_views.xml`, `cmc_reporting_views.xml`, `account_analytic_account_views.xml`, `account_analytic_line_views.xml`, `cmc_menus.xml`, `cmc_cockpit_views.xml`.
New cmc_base models (suggested files, Model's call): `cmc_cost_function.py`, `cmc_responsibility.py`, `cmc_center_cost_report.py` (+ plan hook), `cmc_data_check.py`; migration `migrations/19.0.1.4.0/post-migrate.py`.
cmc_account: `models/account_analytic_line.py`, `models/__init__.py`, new `models/cmc_coge_reconciliation.py`; `security/ir.model.access.csv`, `security/cmc_account_security.xml`; `__manifest__.py`; new `views/cmc_coge_reconciliation_views.xml`.

## 7. Reconciliation checklist (dev-lead, at return)
Names/signatures of sec. 2 by grep; `_cmc_cost_groups` callers unchanged; `group_cmc_user` implies responsible on a migrated db; report rule on `rollup_center_id`; allocation lines skipped by classification; revenue excludes allocation lines; manifest versions 19.0.1.4.0 / 19.0.1.5.0; migration folder name = new version; `py_compile` on touched files.

## Orchestrator decisions (2026-10-09)
- G-1 accepted: CVR wiring of cost-function range warnings moves to WP-10 (cmc_direct_costing v2).
- G-2: COAN double count on 100% distribution across two root plans stays visible as "unexplained"; measure at G-DATA.
- G-3: release note required (allocation lines visible in core project profitability when centers sit on the Project plan).
- WP-13 runs in its own worktree `.worktrees/gap-wp13` (branch feat/gap-wp13) to avoid loading half-written cmc_base/cmc_account code.
