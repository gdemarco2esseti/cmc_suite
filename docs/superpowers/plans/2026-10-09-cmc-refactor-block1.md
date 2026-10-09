# CMC Refactor — Block 1: Defect Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Per task: `odoo-test-writer` writes the RED test from the acceptance criteria below (only in `tests/`), main session verifies it fails for the stated reason, then `odoo-model-dev` makes it green (models/security/data/migrations/manifest). Master plan, invariants and test command: `2026-10-09-cmc-refactor-master.md`.

**Goal:** Close five defects found by the 2026-10-09 rōnin pass before any structural refactor freezes them in.

**Rule for every red test:** reproduce the REAL flow (core methods in core order), never hand-built state in an order core never produces. That shortcut is how T4 stayed hidden. Every test that names a role acts `with_user` as that role.

---

### Task 1: Closing run requires a period (cmc_base, cmc_full_costing)

**Defect:** `cmc.closing.run.date_from/date_to` are optional (`cmc_base/models/cmc_closing.py:45-46`). A run without dates aggregates all history (`account_analytic_account.py:61-64`), and `_check_period_within_closing` skips its check when the closing has no dates (`cmc_full_costing/models/cmc_costing.py:66`). A Q3 costing on an undated closing absorbs years of overhead.

**Acceptance criteria**
- AC1.1 GIVEN no dates WHEN creating a closing run THEN it fails (field required).
- AC1.2 GIVEN a closing Q1 with lines in Q1 and in Q2 WHEN computed THEN only Q1 amounts are allocated (first test with dates: the path was never exercised).
- AC1.3 GIVEN a costing run whose period lies outside its closing's period WHEN created THEN `ValidationError` (the undated shortcut is gone).
- AC1.4 GIVEN an existing DB with an undated closing run WHEN `-u cmc_base` THEN the pre-migration fills `date_from`/`date_to` with min/max `date` of the company's analytic lines (fallback: `create_date::date` for both) so the stored numbers stay exactly the same, and the upgrade succeeds.

**Files**
- Modify: `cmc_base/models/cmc_closing.py:45-46` → `fields.Date(required=True)`
- Modify: `cmc_full_costing/models/cmc_costing.py:60-83` → drop the `closing.date_from and` shortcut
- Create: `cmc_base/migrations/19.0.1.1.0/pre-migrate.py` (SQL, parametrised, per company)
- Modify: `cmc_base/__manifest__.py` version `19.0.1.1.0`
- Modify fixtures (add dates covering their lines): `cmc_base/tests/test_closing.py`, `test_cost_analysis.py`, `test_ux_plan_aware.py`, `cmc_full_costing/tests/test_fc_conservation.py`, `test_fc_security.py`, `test_reclass.py`, `test_costing_levels.py`, `test_costing.py`, `test_fc_extra.py`
- Modify (outside repo, demo seeds): `odoo19/scripts/cmc_seed_full.py`, `cmc_seed_demo.py`, `cmc_seed_year.py`
- Test: `cmc_base/tests/test_closing_period.py` (new)

- [ ] Red: odoo-test-writer writes AC1.1–1.3; run `--test-tags=/cmc_base,/cmc_full_costing`, expect failures on 1.1/1.3 (1.2 may pass: it is a coverage test).
- [ ] Green: odoo-model-dev applies the files above; AC1.4 verified by hand on a copy of `cmc_refactor_base` with one undated run inserted via SQL before `-u`.
- [ ] Full regression green; commit `cmc_base: a closing run always has a period`.

### Task 2: Cockpit KPIs per company (cmc_base)

**Defect:** `cmc_base/models/cmc_cockpit.py:89-92,114-120` `_read_group` over every allowed company and format the sum in `env.company` currency; with EUR + CHF companies active, "Booked Costs" adds CHF to EUR.

**Acceptance criteria**
- AC2.1 GIVEN user with companies A (EUR) and B (CHF) both active, costs 100 in A and 50 in B, current company A WHEN `get_cockpit_data()` THEN Booked Costs = 100 € and the chart shows only A's elements.
- AC2.2 Same for `products_costed` (`cmc.costing.result` count only for current company).

**Files:** Modify `cmc_base/models/cmc_cockpit.py` (add `("company_id", "=", self.env.company.id)` to the three domains). Test: `cmc_base/tests/test_cockpit_company.py` (new, `with_user` + `allowed_company_ids` context).

- [ ] Red → Green → full regression → commit `cmc_base: cockpit figures stay within the current company`.

### Task 3: CVR actual mode derives the fixed cost on create/write (cmc_direct_costing)

**Defect:** `cmc_direct_costing/models/cmc_cvr.py:192-196` `create` never calls `_derive_fixed_cost`; a CVR created by import/RPC in `consuntivo` mode has `fixed_cost=0` → `has_breakeven=True`, `bep_qty=0` shown as fact. Same on `write({"mode": "consuntivo"})`.

**Acceptance criteria**
- AC3.1 GIVEN fixed costs 1000 on center C in the period WHEN `create({mode: consuntivo, center_id: C, dates})` without `fixed_cost` THEN `fixed_cost == 1000` with no button pressed.
- AC3.2 GIVEN a simulation CVR with typed fixed_cost 500 WHEN `write({"mode": "consuntivo"})` THEN fixed_cost re-derived (1000).
- AC3.3 GIVEN actual mode WHEN `fixed_cost` explicitly in vals THEN the explicit value is kept (import of a historical figure).
- AC3.4 Changing `center_id`/`date_from`/`date_to` in actual mode re-derives.

**Files:** Modify `cmc_cvr.py` create/write: derive when mode is consuntivo and `fixed_cost` not in vals and one of `{"mode","center_id","date_from","date_to"}` changed (create: always), before `_build_curve`. Keep the `fixed_cost` plain stored field and its comment (`:41-45`). Test: `cmc_direct_costing/tests/test_cvr_actual_derivation.py`.

- [ ] Red → Green → full regression → commit `cmc_direct_costing: actual-mode CVR derives its fixed cost without a button`.

### Task 4: MRP labour journal-entry twin never enters the cost base (cmc_base, cmc_mrp)

**Defect (verified on core source):** `mrp_account/models/mrp_production.py:137-139` calls `account_move._post()` (analytic lines created) BEFORE writing `time_ids.account_move_line_id`. So `_cmc_mrp_labour_duplicates` (`cmc_mrp/models/account_analytic_line.py:35-48`) finds nothing at classification time, and anyway `_cmc_cost_groups` sums every line of a center, classified or not. Labour is counted twice when the labour accounts carry an analytic distribution to a CMC center. `test_mrp_integration.py:220-229` creates productivity before the analytic line (the reverse of core).

**Acceptance criteria**
- AC4.1 GIVEN a workcenter with analytic distribution to center C AND an analytic distribution model putting the labour account on C WHEN an MO is marked done through the real `button_mark_done` (so core `_post_labour` runs) THEN the closing on C counts labour once (= `wo._cal_cost()`).
- AC4.2 The twin lines are flagged `cmc_excluded=True`, absent from the "Unclassified" queue, and not classifiable (manual classification on an excluded line → `UserError`).
- AC4.3 Non-MRP lines untouched (`cmc_excluded=False` default); conservation tests unchanged.
- AC4.4 Shop-floor user (`mrp.group_mrp_user` only, no CMC group) marks the MO done without AccessError.

**Files**
- Modify `cmc_base/models/account_analytic_line.py`: `cmc_excluded = fields.Boolean(index=True, copy=False, readonly=True)` with docstring "excluded from the cost base (system twin of a line already counted)".
- Modify `cmc_base/models/account_analytic_account.py:_cmc_cost_groups`: base domain `("cmc_excluded", "=", False)`; also the cockpit domains and the exception-queue action domain (`cmc_account/views/cmc_exception_views.xml:11`).
- Modify `cmc_mrp/models/mrp_production.py`: override `_post_labour`: after `super()`, collect `time_ids.account_move_line_id` of the MOs, `sudo()` (`# why`: shop-floor user, CMC metadata only) write `cmc_excluded=True` on their analytic lines.
- Remove `_cmc_mrp_labour_duplicates` and its use (`cmc_mrp/models/account_analytic_line.py:35-48,95-97`): replaced by construction.
- Modify `cmc_mrp/tests/test_mrp_integration.py:220-229`: drop the hand-built order; the new test uses the real flow.
- Versions: `cmc_base`, `cmc_mrp`, `cmc_account` → `19.0.1.1.0`.

- [ ] Red (AC4.1 must fail with labour counted twice) → Green → full regression → commit `cmc_mrp: the labour journal entry twin is excluded from the cost base by construction`.

### Task 5: Manual classification is stored per analytic line, not per journal item (cmc_account)

**Defect:** `cmc_account/models/account_analytic_line.py:115-138` mirrors a manual classification onto the journal item (`account.move.line`, 1:N with analytic lines). Lines 172-181 restore it onto every sibling on reclassify / reset-to-draft+repost: a 60/40 split X/Y classified by hand only on X spreads to Y; two different manual classifications → last write wins, the other is lost.

**Acceptance criteria**
- AC5.1 GIVEN a vendor bill line split 60% center X / 40% center Y, controller classifies only X by hand WHEN the bill is reset to draft and reposted THEN X keeps its manual classification and Y is classified by the rules (not manual).
- AC5.2 GIVEN X and Y classified by hand with different elements WHEN reset+repost THEN both survive.
- AC5.3 GIVEN 100% distribution (today's test) THEN behaviour unchanged.
- AC5.4 Controller (`group_cmc_user`, no accounting write) can classify; acts `with_user`.

**Files**
- Create model `cmc.manual.classification` in `cmc_account/models/cmc_manual_classification.py`: `move_line_id` (m2o, required, ondelete cascade, index), `center_id` (m2o `account.analytic.account`, required), `company_id` (related stored), `cmc_cost_element_id`, `cmc_behavior`, `cmc_directness`; `models.UniqueIndex("(move_line_id, center_id)")`; ACL `group_cmc_user` rw, `ir.rule` `parent_of company_ids`.
- Replace `_cmc_store_manual_classification` / restore (`:115-138`, `:172-181`) with upsert/lookup keyed by `(move_line_id, cmc_center_id)`.
- Remove the four mirrored fields from `cmc_account/models/account_move_line.py`.
- Create `cmc_account/migrations/19.0.1.1.0/post-migrate.py`: for each move line with `cmc_classified_manually`, create one row per analytic line of that move line that is itself `cmc_classified_manually` (keeps exactly what is manual today); then drop the old columns.
- Test: `cmc_account/tests/test_manual_split.py`.

- [ ] Red (AC5.1/5.2 fail) → Green → full regression → commit `cmc_account: a manual classification belongs to its analytic line, not to the whole journal item`.

---

### Task 6: capacity refresh on multi-line batches (cmc_base)
`_cmc_capacity_rows` called core `_get_analytic_accounts()` (singleton-only) on a recordset → `Expected singleton` on any CRUD touching fixed lines on 2 centers. Test: `cmc_base/tests/test_capacity_multi_line.py`.

### Task 7: CMC controller can classify bill lines (cmc_account) — AD-007
Covered by `test_controller_with_cmc_rights_only_classifies_bill_line` in `cmc_account/tests/test_manual_split.py`; done together with Task 5.

### Task 8: one sign convention (all modules) — AD-005
Dispatch plan from odoo-dev-lead (inventory of every `amount` read, canonical `-amount` in `_cmc_cost_groups`, Cost Entry positive input, fixture helper, migration predicate). Includes T3 review leftovers (CVR derived CF sign, rename context `cmc_cvr_skip_curve`, batch derive without O(n²), `p - v` in curve).

## Block 1 exit (checkpoint with user)
- [ ] Full regression: 241 + new tests, 0 failed.
- [ ] `odoo-qa` on the 5 commits (failures/tracebacks only).
- [ ] Rōnin on the block-1 diff (it CERTIFIES fixes → mandatory).
- [ ] Report to user; write block-2 plan.

#### Task 8 dispatch contract (odoo-dev-lead, 2026-10-09)
- Canonical helper `account.analytic.line._cmc_cost_read_group(domain, groupby=())` in `cmc_base/models/account_analytic_line.py`: same shape as `_read_group(..., aggregates=["amount:sum"])`, last element = cost = `-(sum or 0.0)`. ONLY place with `-amount`. Used by `_cmc_cost_groups`, cockpit (2 calls), cmc_abc `_cmc_activity_pools`. No `abs(` on amounts left in `cmc_*/models` (except closing pivot, cmc_utils).
- `account.analytic.line.cmc_cost_amount`: Monetary "Cost", compute/inverse = `-amount`, not stored. Editable in the dedicated Cost Entry list/form (replaces `amount`, `sum="Total"`); readonly + `invisible="not cmc_behavior"` on the extended native form (native `amount` stays the editable field there).
- Migration `cmc_base/migrations/19.0.1.2.0/post-migrate.py` (manifest 19.0.1.2.0): flip `amount` where `amount > 0` AND CMC-classified (`cmc_behavior`/`cmc_cost_element_id`/`cmc_activity_id` if column exists) AND no source document (`general_account_id`, `move_line_id`, `project_id`, `employee_id`, `cmc_mrp_move_id` NULL when columns exist, `COALESCE(category,'other')='other'`); log count + ids; recompute stored capacity fixed costs. Release note: draft + recompute closings/costings/CVR computed on mixed data.
- Tests: flip sign inside local helpers (call sites unchanged); rewrite `cmc_abc test_negative_amount_pool` as "a reversal reduces the pool"; cmc_mrp twin asserts become `+LABOUR_COST`; new red tests (a) cost field round-trip, (b) reversal reduces cost in closing/cockpit/abc, (c) migration predicate.
- Sequence: test-writer → model-dev → view-dev → qa. ~6.5h. Plus T3 leftovers in cmc_cvr.py.

## Open issues found after block 1 (all closed in block 5: 5747a78 queue list view, eaff3b8 i18n, seeds patched out of repo with costing dates + CMC_SEED_DB/CMC_SEED_ADDONS overrides)
- Unclassified queue (`cmc_account.action_cmc_unclassified_lines`) opens with the **timesheet** list view when `hr_timesheet` is installed (no `view_id` on the action → higher-priority hr_timesheet list wins: "Time Spent" column, no cost). Give the action a dedicated list view (center, description, element, `cmc_cost_amount`, date). Found while recording the block-1 video guide.
- New field labels not yet translated (`Cost` / `cmc_cost_amount`): i18n regen in block 5.
- Demo seed scripts (`odoo19/scripts/cmc_seed_*.py`) not plan-aware and partly broken (costing run dates): block 5.
