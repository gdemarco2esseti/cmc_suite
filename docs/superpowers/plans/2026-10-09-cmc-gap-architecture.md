# CMC suite - gap-closure architecture (AD-101 .. AD-119)

> Author: software-architect (read-only pass), 2026-10-09. Branch `feat/gap-manuali`.
> Input: `docs/analysis/gap-manuali-2026-10/10_gap_register.md` (126 in-scope capabilities, 102 gaps,
> WP-01..WP-16), `11_oca_scout.md`, `00_suite_inventory.md`, master plan AD-001..AD-007, release notes.
> Scope: the **system shape** only (modules, depends, shared foundations, data sources, NFR, waves, scope
> cuts). Model/field/method names, xmlids, ACL rows and file partition belong to `odoo-dev-lead`, who
> decomposes each WP **inside** these constraints. Odoo 19 (`.claude/odoo-profile.md`), manifest
> `19.0.x.y.z`, LGPL-3 (OPL-1 only for Enterprise bridges).
>
> Existing decisions stay binding: AD-001 (computed run locked, reset deletes results), AD-002 (no reset of a
> run read by a computed run), AD-005 (cost = negative analytic amount, read as `-amount` in one place),
> AD-006/AD-007, bridge profiles (`_cmc_bridge_profiles`), `CMC_RUN_STATE`/`CMC_AUTO`/`CMC_GATE` sentinels,
> record rules out of `noupdate`, engines read analytic lines in `sudo()` inside the run's company perimeter.

## Measured data shape (evidence for the invariant contracts)

Read-only SQL on `cmc_all` (seeded demo, 4 companies; the only CMC database with data). These numbers prove
that each case **occurs**; a client production database must be measured again before wave B (gate G-DATA, AD-117).

| Measure | Value | Used by |
|---|---|---|
| Income-account journal items posted / without analytic distribution | 56 / **40 (71%)** | AD-111 (revenue not from analytic) |
| Customer documents posted: invoices / credit notes | 34 / **8 (19%)** | AD-111 (reversal is normal, not an edge case) |
| Posted moves dated >31 days before their creation (back-dated) | **19 / 84 (23%)** | AD-111, AD-113 (late postings into computed periods) |
| Analytic lines / with a move line / positive amount | 852 / 28 / 335 | AD-110, AD-112 |
| Posted GL span | **275 days** | AD-116 (a 3-year series cannot come from the GL alone) |
| Done MOs / unbuild orders / scraps | 4 / 0 / 0 | sample too small: MRP reversibility (unbuild) is decided from core behaviour, re-measure at G-DATA |
| `stock_valuation_layer` table | **absent in v19** | AD-111: valuation is read from the v19 stock-move valuation API (verify on source), never from SVL |

---

## AD-101 - Module topology: 4 in-place extensions, 10 new modules, 3 WP merges

- **Context**: 16 WPs; the suite has one module per methodology plus thin bridges (`cmc_account`, `cmc_hr_timesheet`, `cmc_mrp`). 少: no module a requirement does not ask for; 美: follow the existing "one methodology = one module, bridges stay thin" pattern.
- **Options**: (a) one module per WP (16 new); (b) everything in a few big modules (`cmc_planning`, `cmc_analysis`); (c) in-place where the WP deepens an existing methodology, new module where it is a new methodology, merge WPs that are meaningless apart.
- **Decision**: (c).

| WP | Where | Why |
|---|---|---|
| WP-01 | in-place `cmc_base` (+ `cmc_account` for FIN-13) | hardening of the core |
| WP-02 | **new `cmc_budget`** (also owns FCS-04 cash forecast, moved from WP-13) | new methodology; FCS-04 needs revenue budget + payment terms, both here |
| WP-03 | **new `cmc_standard_cost`** | needs MRP + budget rates; not every full-costing user runs standards |
| WP-04 | in-place `cmc_full_costing` | same engine, deeper inputs |
| WP-05 + WP-15 | **new `cmc_job_costing`** (WP-05 wave B, WP-15 added in place wave C) | EVM is the control layer of the same job; one module on `project` instead of two |
| WP-06 | **new `cmc_process_costing`** | distinct methodology; keeps `cmc_mrp` a feeder bridge |
| WP-07 + WP-08 | in-place `cmc_abc` (v2 engine, then ABM) | ABM has no meaning without the ABC model |
| WP-09 | **new `cmc_profitability`** (also owns FIN-12 SBU, moved from WP-13) | revenue + cost-to-serve; SBU margin needs the profit facts, not the GL |
| WP-10 | in-place `cmc_direct_costing` | decisions are the existing CVP/differential, persisted and deepened |
| WP-11 | **new `cmc_scenario`** (FCS-03 moved to WP-12) | optional advanced engine (Monte Carlo); keeps `cmc_base` light |
| WP-12 | **new `cmc_abb`** (also owns FCS-03 driver what-if) | ABB and ABC what-if are the same reverse computation (forecast driver qty x stable rates) |
| WP-13 | **new `cmc_fin_analysis`** | GL-based, independent of the cost pipeline |
| WP-14 | **new `cmc_kpi`** | reads budget + financial analysis |
| WP-16 | **new `cmc_target_lcc`** | reads only foundation registries |

- **Motivation**: 10 new modules instead of 16; each new module is installable alone with its declared depends. The moves (FCS-03, FCS-04, FIN-12, WP-08, WP-15) put each capability where its data already is, so no glue module is needed.
- **Consequences**: register WP columns are re-mapped as above (capability counts per WP change; total stays 102). `cmc_abc` v2 is an engine rewrite: computed v1 runs stay readable and the v2 engine must reproduce v1 figures on v1-shaped inputs (regression test, not a migration of results). `-u` in the test command and in release notes must list every module (README rule).
- **Traceability**: all WP-01..WP-16; CAP ids per AD-118.

## AD-102 - Depends graph: consumers never depend on producers; `cmc_suite` installs every CE module

- **Context**: without care, quote costing (WP-10), job costing (WP-05), target costing (WP-16), profitability (WP-09) and ABC-11 would each depend on the engines that compute rates/unit costs, which creates a dense, near-cyclic graph.
- **Options**: (a) direct depends on producer modules; (b) glue auto_install modules per pair; (c) shared registries in `cmc_base` (AD-107 measures, AD-108 rates) written by producers, read by consumers.
- **Decision**: (c). Final graph (new depends in **bold**):

```
analytic, product ──> cmc_base ──> cmc_full_costing | cmc_direct_costing | cmc_abc | cmc_tdabc | cmc_joint_costs
                                   cmc_scenario | cmc_target_lcc                          (depends: cmc_base only)
account ──> cmc_account (cmc_base) ──> cmc_hr_timesheet (+hr_timesheet) ──> cmc_job_costing
                                   ──> cmc_mrp (+mrp_account) ──> cmc_process_costing
                                   ──> cmc_budget ──> cmc_standard_cost (+cmc_mrp)
                                                  ──> cmc_abb (+cmc_abc)
                                   ──> cmc_fin_analysis ──> cmc_kpi (+cmc_budget)
cmc_profitability: cmc_abc, cmc_account, cmc_direct_costing, cmc_fin_analysis
cmc_timesheet_grid (EE, OPL-1, auto_install): timesheet_grid, cmc_hr_timesheet   (unchanged)
cmc_suite: every CE module above (9 existing + 10 new = 19 depends); never an EE/OCA module
```

- **Motivation**: 軽 - no module depends on a costing engine to read a rate; the deepest chain is 4 levels (`cmc_base > cmc_account > cmc_budget > cmc_standard_cost`). No cycle.
- **Consequences / constraints**: no new module depends on `sale`, `purchase`, `quality_*`, `account_budget`, `account_reports`, `mis_builder`, `date_range` (channel/salesperson come from AD-109 attributes and `invoice_user_id`; supplier data from vendor bills). Adding a depends outside this graph requires a new AD. `cmc_suite` stays `application=True`, no models.
- **Traceability**: WP-01..16.

## AD-103 - Build vs reuse: build in CMC, CE-first, no EE/OCA depends, no bridge in this programme

- **Context**: scout: budget and financial reports exist only in EE (`account_budget`, `account_reports`); OCA 19 has `mis_builder`, `mis_builder_budget`, `account_budget_oca` in `oca/` but not on the suite's addons path; `mis_builder` pulls `board`, `report_xlsx`, `date_range` (3 extra OCA repos). Nothing native/OCA for flexible budget, versions, EVM, transfer pricing, ABC/ABB, target/LCC. Installation must stay self-contained.

| WP | Reuse considered | Verdict |
|---|---|---|
| WP-02 | EE `account_budget` (`budget.analytic`/`budget.line`), OCA `account_budget_oca`, `mis_builder_budget` | **Build.** None models center x element x F/V, variable rate x driver, flexible budget, GPK or versions; EE breaks CE-first. |
| WP-13 | OCA `mis_builder` (+cash flow), EE `account_reports` | **Build** a small scheme/mapping/ratio engine (AD-116). `mis_builder` saves ~40 h but adds 4 OCA repos to every install and its KPI expressions do not cover the override register / reduced mode / choices register of FIN-02. |
| WP-03 | core `mrp_account` BoM cost compute, v19 stock valuation in standard mode | **Reuse core for the write path** (standard_price push goes through the core revaluation, AD-111), build cards/variances. |
| WP-05/15 | core project profitability panel, EE `project_account_budget` | **Build** the job cost sheet (CMC rates + applied overhead), link from the project; do not replace the core panel. |
| WP-09 | core `sale_margin` | Not used: margin is computed from posted invoices (AD-111), `sale` not required. |
| WP-14 | core `spreadsheet_dashboard`, OCA `kpi` | **Build** KPI definitions; standard views (graph/pivot) only. |

- **Decision**: build every WP inside CMC with core-CE depends only. No optional EE/OCA bridge is part of this programme.
- **Deferred bridges** (each needs its own AD and a client asking for it): `cmc_mis_builder` (export CMC aggregates as MIS data source), `cmc_account_budget` (EE: import/export `budget.line` to a CMC version), `cmc_quality` (EE `quality_control` defects as a measure feeder for COQ/KPI-02).
- **Traceability**: WP-02, 03, 05, 09, 13, 14, 15; scout `11_oca_scout.md`.

## AD-104 - Foundation: one planning version concept in `cmc_base`, locked with `cmc.run.mixin`

- **Context**: budget (WP-02), forecast (FCS-01), standard cards (WP-03), project baselines (WP-15), ABB (WP-12), scenarios (WP-11), KPI targets (WP-14) and planned rates/measures (AD-107/108) all need "a named, dated, approvable set of planned figures". Built per WP it would exist 6 times.
- **Options**: (a) each module its own header; (b) a new `cmc_plan` core module; (c) one version model in `cmc_base`.
- **Decision**: (c) one version model in `cmc_base`: company, span (date_from/date_to), type (base ships `budget`, `forecast`, `standard`, `simulation`; owning modules add more with `selection_add`, e.g. `baseline` in `cmc_job_costing`), copy-from (parent) link, assumption register (free notes per version), version currency + one fixed internal rate to company currency (INT-07).
- **Motivation**: a `cmc_plan` module would be required by WP-02/03/04/05/07/09/10/12/14/15/16 - i.e. by everything - so it would be `cmc_base` under another name (少). Reusing the run mixin gives the lock for free (美).
- **Contratto d'invarianti** (reuse of `cmc.run.mixin`, a CMC model): (1) *done = locked inputs* - holds: approved version = `done`, its plan lines are run-line-mixin input lines; (2) *reset deletes results* - holds trivially: a version declares no result fields, reset deletes nothing; (3) *AD-002 dependents* - must be implemented: an approved version cannot be reset while a computed run (variance, standard roll-up, EVM, ABB) reads it; (4) *state labels "Draft/Computed"* - violated for versions ("Approved"); the dev-lead may relabel in the view, not change the selection keys.
- **Consequences / constraints**: only `group_cmc_manager` approves/resets a version; drafts edited by `group_cmc_user`. Copy duplicates lines (set-based create) into a new draft version. No multi-step approval workflow, no consolidation in base (INT-04 consolidation is WP-11).
- **Traceability**: BUD-01, BUD-03, FCS-01, STD-01, PRJ-02, INT-04, INT-07; WP-02/03/11/12/14/15.

## AD-105 - Time grain: calendar month, no period model, no `date_range`

- **Context**: closing runs already carry date_from/date_to; budgets phase monthly (BUD-02), BvA is monthly/YTD (BUD-04), EVM per period (PRJ-03/04), KPI trend monthly. OCA `date_range` is not on the path; core has no period model.
- **Options**: (a) a CMC period model; (b) depend on `date_range`; (c) month bucket = Date (first day of month) on every plan/fact row, headers keep date_from/date_to.
- **Decision**: (c). One pure helper in `cmc_utils` lists months of a span and phases an annual amount by weights through the existing `spread()` (conserving). Seasonality profiles (12 weights) live in `cmc_budget`. Fiscal years for financial analysis come from the company fiscal-year end (`account`), not from CMC.
- **Motivation**: 少 - no master data to maintain, no dependency; `read_group` on `date:month` and Date equality are index-friendly.
- **Consequences**: weekly/daily granularity is out (STD-04 daily/weekly team variances cut, AD-118). Multi-year plans (BUD-03) use the same month grain.
- **Traceability**: BUD-02, BUD-04, FCS-01, PRJ-03, KPI-01.

## AD-106 - Foundation: cost-object registry, stamped on the analytic line

- **Context**: cost objects today are a non-stored-friendly `Reference` (`cmc_cost_object_ref`, informational), ABC = products only, TDABC = product/customer by Selection. WP-04 (actual prime cost per finished product), WP-05 (job), WP-07 (product/customer/channel/order/supplier/project, CO->CO), WP-09 (layered profitability), WP-16 need one key.
- **Options**: (a) keep `Reference` fields; (b) one M2o column per object type on every fact table; (c) a registry model of cost objects (type + Many2oneReference to the record + company), one M2o everywhere.
- **Decision**: (c) in `cmc_base`, with a stored, indexed M2o to it on `account.analytic.line`, filled by the bridges (`cmc_mrp`: the **finished** product of the MO on MP/MOD lines; `cmc_hr_timesheet`: the project; classification rules may set it) and migrated from `cmc_cost_object_ref`.
- **Motivation**: groupable in `read_group`/SQL (a `Reference` is not), one join for every engine, new types without schema change.
- **Contratto d'invarianti**: (1) *unicity* - registry key (type, model, record, company) must be unique at source: enforced by a unique index; (2) *ownership* - the referenced record (product, partner, project) is owned by core: deletion of a referenced record with computed results is blocked (existing pattern of the release notes §8); (3) *ordering/history* - **hierarchies are NOT stored on the cost object** (no parent_id): a customer's channel or a product's line changes over time and a stored parent would rewrite computed runs. Hierarchy = attribute values (AD-109) read at compute time and **snapshotted on the result rows**; (4) *semantics of `account.analytic.line.product_id`* - on MP lines it is the raw material, not the cost object: no engine may use it as the cost object; (5) *cardinality* - objects are created lazily, only for records that receive cost or revenue in a run (bounded by period transactions), never bulk for all partners/products.
- **Consequences**: `cmc_tdabc` and `cmc_joint_costs` keep their own keys (COVERED, untouched); `cmc_abc` v2 moves to the registry. Migration of `cmc_cost_object_ref` in `cmc_base` (log unmappable values).
- **Traceability**: FC-04, FC-06, ABC-06, ABC-07, PRF-01..06, TGT-01, LCC-01; WP-04/05/07/09/16.

## AD-107 - Foundation: one measure catalogue (= the driver catalogue) and one quantity fact table

- **Context**: driver quantities are typed per run (full costing, ABC); WP-02 needs actual volumes (flexible budget), WP-04 produced quantities, WP-07 feeder-loaded driver quantities (INT-02), WP-08 activity performance measures, WP-14 non-financial KPIs, WP-12 forecast volumes. A KPI/measure registry next to the driver catalogue would duplicate it.
- **Options**: (a) driver catalogue + separate KPI measure registry; (b) one catalogue: `cmc.cost.driver` extended to cover every non-monetary measure (kind: allocation driver / statistical measure; type transaction/duration/intensity; UoM; source manual/feeder), plus **one** fact table (measure x month x company x [version] x [center] x [cost object] = quantity).
- **Decision**: (b) in `cmc_base`. Actual quantities have no version; planned quantities belong to a version (AD-104). Feeders are declared by bridges through a profile hook in the style of `_cmc_bridge_profiles()` (model, domain, count/sum field, object mapping) and refreshed by a cron with `read_group`; `cmc_mrp` declares produced quantity, routing time, scrap; `cmc_account` declares invoice/order-line counts per customer/supplier/product; `cmc_hr_timesheet` hours per project/center.
- **Motivation**: 美 - a driver *is* a measure; KPI definitions (WP-14) only add formula/target/threshold on top. Engines read quantities without depending on feeders (AD-102).
- **Contratto d'invarianti** (reuse of `cmc.cost.driver`): (1) *unit* - holds (UoM present); (2) *company scope* - holds (parent_of rule); (3) *a driver implies an allocation base* - no longer true for statistical measures: engines that allocate must filter on kind, KPI may use both.
- **Consequences / constraints**: runs **copy** the quantities they use into their own input lines at Compute (AD-113): a feeder refresh never changes a computed run. ABC-specific driver fields (level, weighted index components) are added by `cmc_abc` via `_inherit`. Feeder refresh is idempotent (replace the month's actual facts of that feeder, never append).
- **Traceability**: INT-02, INT-05, FC-05, STD-02, ABC-08, ABM-03, KPI-02, BUD-06.

## AD-108 - Foundation: rate and unit-cost registry (producers publish, consumers read)

- **Context**: per-unit values are needed across WPs: predetermined center rates (FC-08, GPK ALC-06), resource hourly rates (CEN-04), activity rates (ABC-04), unit cost per product (FC-05, ABC-04), standard card unit cost (STD-01), service unit cost (ALC-07). Consumers: job costing, quote costing (DIF-04), make-or-buy hidden costs (DIF-03), ABC vs traditional (ABC-11), profitability, target/feature costing (TGT-02), LCC, ABB, EVM rate cards.
- **Options**: (a) consumers depend on producers; (b) one registry in `cmc_base`: holder (center / activity / resource / cost object), driver/unit, month validity, F/V component, element (optional), value, origin = a computed run (published at Compute) **or** an approved version (planned).
- **Decision**: (b).
- **Motivation**: 軽 - removes 6 cross-module depends (AD-102); 美 - "standard vs actual" becomes "planned rate vs computed rate" on the same key.
- **Consequences / constraints**: rates published by a run are result rows of that run (written only under `CMC_RUN_STATE`, deleted by its reset, AD-001). A consumer run records which rate rows/origins it read; the producer's `_cmc_check_no_dependents` refuses reset while such a consumer is computed (AD-002, implemented once in base, generic over origins). Rates of an approved version are locked with it. One rate per (holder, unit, component, origin, month): unique index.
- **Traceability**: FC-08, CEN-04, ABC-04, ABC-11, STD-01, ALC-07, DIF-03, DIF-04, TGT-02, PRJ-01.

## AD-109 - Foundation (wave B0): one attribute framework, selection-valued

- **Context**: ABM attributes (VA/NVA, efficiency 1-5), cost-of-quality category (COQ-01), product line/brand, customer segment/channel/market (PRF-01, ABC-06), strategy fit, sustaining hierarchy.
- **Options**: (a) dedicated fields per concept; (b) polymorphic (model, id, attribute, value) table; (c) attribute + ordered values (with optional numeric weight) assigned through M2M per target model (activity, product template, partner, center).
- **Decision**: (c) in `cmc_base`, built at the start of wave B (first consumer WP-07/09). One value per attribute per record (constraint). Numeric scales are ordered values with a weight.
- **Motivation**: (b) cannot be joined efficiently in `read_group`; (a) multiplies fields. Channel/segment as attributes avoid depending on `sale`/`crm` (AD-102).
- **Consequences**: computed results snapshot the attribute values they grouped by (AD-106 inv. 3). Decision flags of CLS-04 (controllable, avoidable, committed) stay plain fields on the cost element: engines filter on them.
- **Traceability**: ABM-02, COQ-01, ABC-05, ABC-06, PRF-01, PRF-03; WP-07/08/09.

## AD-110 - Plan figures are never stored as analytic lines

- **Context**: tempting 少 option for WP-02: store budget/forecast as `account.analytic.line` with a flag, so BvA is one table.
- **Options**: (a) analytic lines flagged "plan"; (b) plan lines in their own table keyed like the actuals (center, element, behavior, month, cost object, version).
- **Decision**: (b).
- **Contratto d'invarianti** (reuse of `account.analytic.line` for plan data): (1) *proprietà del dato* - core code treats every analytic line as an **actual**: project profitability panel, timesheet reports, analytic reports, CMC closing/capacity/ABC pools (`_cmc_cost_groups` reads all non-excluded lines) - **violated**: a plan line would be counted as actual cost in at least 5 places; excluding it would need a filter in every core and CMC reader; (2) *volume* - 852 actual lines vs a budget of centers x elements x 12 months x versions: plan rows would outnumber actuals and slow every actual read; (3) *company/currency* - analytic is company currency only, plan needs a version currency (INT-07). Invariant (1) violated => model not used.
- **Consequences**: BvA joins two sources in a reporting SQL view (AD-114). Plan lines store the natural sign (AD-112).
- **Traceability**: BUD-01..04, FCS-01, STD-02/03.

## AD-111 - Data sources per engine (one reader per source)

- **Context**: engines must not each re-invent how to read the ledger.

| Need | Source | Reader (single place) |
|---|---|---|
| Costs by center x element x behavior | `account.analytic.line` (cost = `-amount`, AD-005), excluded lines out | existing `_cmc_cost_groups` (cmc_base), `sudo()` in company perimeter |
| Direct cost per cost object (prime cost, job, CO trace) | analytic line + cost-object M2o (AD-106) | one new grouped reader next to `_cmc_cost_groups` |
| Revenue (profitability, BvA revenue, profit centers, DC-02, cash forecast) | **`account.move.line`**, posted, income accounts, revenue = **`-balance`**, all move types (credit notes net automatically) | one reader in `cmc_account` |
| GL balances (FIN-*, FIN-13) | `account.move.line` posted, `read_group` by account and month; balance sheet = cumulative to date, P&L = within fiscal year | one reader in `cmc_account` (used by `cmc_fin_analysis`) |
| Quantities (produced, routing time, scrap, counts) | `stock.move`/`mrp.production`/`mrp.workcenter.productivity` (via `cmc_mrp`), invoice lines (via `cmc_account`), timesheets | AD-107 feeders into the quantity fact table |
| Inventory value / standard push | v19 stock valuation API (no `stock.valuation.layer` in v19) | `cmc_standard_cost` / `cmc_mrp`; verify the API on source (context7) before WP-03 |

- **Contratto d'invarianti** (reuse of `account.move.line` as revenue/GL source): (1) *completeness* - is revenue complete on the analytic side? **No: 40/56 income lines (71%) carry no analytic distribution** => revenue is read from the GL, never from analytic lines; revenue without product/customer lands on an explicit "unassigned" object so PRF-01 reconciles to the P&L; (2) *reversibility* - posted documents are reversed (8 credit notes on 42 customer documents, 19%) and can be reset to draft/cancelled => only `parent_state = posted`, credit notes netted by sign, never filtered out; (3) *ordering* - **23% of posted moves are back-dated >31 days** => a computed run is a snapshot (AD-113) and late postings show up as a reconciliation difference, never silently; (4) *ownership* - the GL is the truth; CMC never writes journal entries (no WIP/FG posting by CMC: FC-09 uses the core valuation path); (5) *cardinality* - unbounded => aggregated by `read_group` at the fact grain (AD-114), never browsed line by line.
- **MRP** (`stock.move` as quantity source): *reversibility* - an unbuild order reverses a produced quantity: feeders net unbuild moves (sample too small to measure, 0 unbuilds in `cmc_all`: re-measure at G-DATA); *unicity* - MO quantities are read from done moves, idempotent per move (existing `cmc_mrp` pattern).
- **Traceability**: FC-04, FC-05, DC-02, PRF-01..06, BUD-04, FCS-04, FIN-01..13, INT-02.

## AD-112 - Sign conventions: stored natural, shown positive, variance positive = favourable

- **Decision**: every stored amount keeps Odoo's natural sign: cost negative, revenue positive (analytic `amount`, plan lines, profit facts). Users enter costs and revenues as positive figures per kind and the system stores the sign (AD-005 pattern). Engines convert in their one reader (`-amount` for cost, `-balance` for GL revenue). GL figures in `cmc_fin_analysis` keep `balance` (debit positive); each scheme line carries its presentation sign.
- **Motivation**: with natural signs `actual - plan > 0` is **favourable for both** costs and revenues: one variance formula, no `abs()`, SQL union of plan and actual without sign flips.
- **Consequences**: margins = revenue - cost on displayed positives; variance labels F/U derive from the sign, not from the line kind. Imported revenues on analytic lines stay positive (release notes §1).
- **Traceability**: STD-03, BUD-04, CAP-04, STD-04, PRF-01.

## AD-113 - Every new engine is a `cmc.run.mixin` run; results are snapshots

- **Decision**: every new computation (budget variance, flexible budget, GPK closing, forecast generation, standard roll-up, MO variances, absorption, job cost sheet, process costing, ABC v2, profitability, decisions, simulation, ABB, financial analysis, KPI pack, EVM status, target/LCC) is a run with `cmc.run.mixin` / `cmc.run.line.mixin` / `cmc.run.result.mixin`, compute from draft, locked when done, reset deletes results, `CMC_RUN_STATE` for result writes, `FOR UPDATE` after flush. Pure live **reports** (BvA, hierarchical cost pivot) are SQL views, not runs (AD-114).
- **Constraints**: a run copies at Compute the quantities and rates it uses into its input/result rows (AD-107/108) so a later feeder refresh or a back-dated posting never changes a computed figure; dependencies between runs/versions are guarded by AD-002 (`_cmc_check_no_dependents`). Each run stores the totals it reconciles to (Σ in = Σ out, INT-05) and fails loud when conservation breaks. Engines split into pure steps (block 4 style), `spread()`/`largest_remainder` for every split.
- **Traceability**: AD-001, AD-002; INT-04, INT-05.

## AD-114 - Performance: aggregated facts, set-based reads, bounded Python

- **Unbounded relations** (grow with time/transactions): `account.analytic.line`, `account.move.line`, `stock.move`, quantity facts (AD-107), profit facts (WP-09), plan lines x versions, simulation samples (WP-11).
- **Constraints**: (1) every read of those tables is `read_group`/`_read_group` or `SQL` grouped at the target grain (center x element x month; product x commercial partner x month; account x month) - no `search()` + loop over lines, no `mapped()` over unbounded recordsets; (2) fact grain of WP-09 = product x commercial partner x salesperson x month, **not** invoice line; drill-down opens the journal items by domain (PRF-06), nothing copied; (3) Monte Carlo samples are not stored per iteration: only statistics/percentiles and the tornado table (N capped, e.g. <= 10,000, fixed seed per run); (4) creates are batched (`create(vals_list)`); (5) composite indexes on the fact keys (company, month, version/measure); (6) Python tree walks are allowed only over bounded master data (centers, activities, cost-object network nodes in one run) - the ABC network solver works on aggregated pools, not lines; (7) live BvA = SQL view (`_auto = False`) over plan lines UNION aggregated analytic actuals at center x element x month, with company rule; (8) feeders run in a cron per company and month, idempotent.
- **Traceability**: PRF-06, BUD-04, CEN-02, RSK-01, INT-02.

## AD-115 - Security and multi-company: reuse the two CMC groups, add exactly one read-only group

- **Decision**: `group_cmc_user` computes/edits drafts, `group_cmc_manager` configures schemes/rules and approves/resets versions. One new group, **`CMC responsible` (read-only, own responsibility centers)**, implied by `group_cmc_user`, required by BUD-05: its record rules restrict budgets/actuals/variances to the centers of responsibility centers the user owns; `group_cmc_user` gets an unrestricted rule so the union lifts the filter.
- **Constraints**: every new model has `company_id` and a global `ir.rule` `company_id parent_of company_ids`, rules **not** `noupdate`, `_check_company_auto`, two-company `with_user` tests (master-plan invariant). Engines read analytic and GL in `sudo()` inside the run's company perimeter with a `# why`, output aggregates only; **drill-down** to journal items runs as the user (core accounting rights apply, no sudo). Benchmarks across companies (ABM-05, KPI) read only the companies the user is allowed (`env.companies`). Each new module ships ACLs for the two groups (results read-only for users, as today).
- **Traceability**: BUD-05, CEN-03, INT-06, FIN-*, ABM-05.

## AD-116 - Financial analysis: own scheme/mapping/ratio engine on GL balances, with historical import

- **Context**: AD-103 rejects `mis_builder`/`account_reports`. FIN-05 needs >= 3 years; the GL in `cmc_all` spans **275 days** and a client going live on Odoo has no prior years.
- **Decision**: `cmc_fin_analysis` = (1) reclassification schemes (lines, aggregates, presentation sign) with account mapping rules (code prefix / account type / tag) + per-account overrides + reduced mode + choices register, shipped with Italian default schemes as data (financial and functional BS, value-added and cost-of-sales P&L, contribution P&L at company level using the account -> element -> behavior mapping of `cmc_account`); (2) ratios as expressions over aggregate codes evaluated with core `safe_eval` (namespace = aggregate values only); (3) a per-year **historical balance import** (account x year) used when the GL has no data for that year; (4) one run per company x span (AD-113).
- **Motivation**: smallest engine that covers FIN-01..11 without 4 OCA repos; `safe_eval` is the core mechanism, no custom parser.
- **Consequences**: sector benchmarks (FIN-05) and WACC inputs (beta, rf, Kd) are typed/imported reference data (no external feed). Crisis indicators = ratios with thresholds. Cash-flow statements derive from BS deltas + P&L non-monetary items of the same run.
- **Traceability**: FIN-01..11, FIN-13 (reader shared), FCS-04 (in `cmc_budget`), FIN-12 (in `cmc_profitability`).

## AD-117 - Wave plan: A0 foundations, A, B0, B, C

| Wave | Content | Gate to start |
|---|---|---|
| **A0** | AD-104 version, AD-105 month helper, AD-106 cost objects (+ analytic M2o, `cmc_mrp`/`cmc_hr_timesheet` stamping, migration), AD-107 measures + feeders hook + `cmc_mrp`/`cmc_account` feeders, AD-108 rate registry, AD-111 revenue/GL readers in `cmc_account` | full regression green (390) |
| **A** | WP-01 (`cmc_base`/`cmc_account`), WP-02 (`cmc_budget`, without FCS-04), WP-04 (`cmc_full_costing` v2), WP-13 (`cmc_fin_analysis`) - parallel after A0; WP-01 first among them for CLS-02 cost functions used by WP-02/04 | A0 merged |
| **B0** | AD-109 attribute framework; **G-DATA**: measure AD-111 invariants on a client production database (backdating, credit notes, unbuilds, income lines without analytic) | wave A merged |
| **B** | WP-07 (`cmc_abc` v2) first; then WP-09 (`cmc_profitability`) and WP-10 (`cmc_direct_costing` v2) on its rates; in parallel WP-03 (`cmc_standard_cost`), WP-05 (`cmc_job_costing`), FCS-04 in `cmc_budget` | B0 |
| **C** | WP-08 (in `cmc_abc`), WP-12 (`cmc_abb` + FCS-03), WP-15 (in `cmc_job_costing`), WP-06 (`cmc_process_costing`), WP-16 (`cmc_target_lcc`), WP-11 (`cmc_scenario`), WP-14 (`cmc_kpi`) last | wave B merged |

- **Changes vs the register**: the A0 foundation block is added (otherwise WP-02/04/07 build three incompatible versions of versions, quantities and rates); WP-10 moves after WP-07 inside B because DIF-03/DIF-04 need published activity rates; WP-14 stays last (reads budget, fin analysis, measures).
- **Process per WP**: `odoo-dev-lead` decomposition -> worktree -> TDD -> review + rōnin gate -> merge, as in the refactor master plan.

## AD-118 - Scope cut per WP: minimum for COVERED (strict register definition) vs skip

"Must" = what makes every capability of the WP COVERED (UI + engine on system data). "Skip" = not built in this programme.

| WP | Must (COVERED) | Skip |
|---|---|---|
| WP-01 | CEN-02 roll-up over `cmc_parent_id` (not the plan tree: centers span plans) + hierarchical pivot; CEN-03 responsibility centers (type, owner, M2M centers), controllable flag on element, controllable-cost report, ROI with invested capital typed per period; CLS-02 cost function per center x element (K, v, step table, range) with high-low and OLS on monthly history + range warnings used by CVR derive and WP-02; CLS-04 flags on element; ALC-01 allocation becomes a run that posts system CMC analytic lines read by closing; ALC-04 manual sequence + order by service share; ALC-08 criterion/rationale/traced flag + allocated-share report; CAP-01 normal + budget levels; BUD-05 (AD-115); FIN-13 COGE<->COAN reconciliation (cmc_account); INT-05 driver-vs-feeder and prior-period reasonableness warnings | approval workflows, org charts, R² diagnostics beyond the fit value |
| WP-02 | BUD-01 version lines center x element x F/V (+ responsibility center), fixed + var rate x planned driver, company total check; BUD-02 seasonality profiles; BUD-03 multi-year version split into annual versions + gap to target profit; BUD-04 BvA view monthly/YTD/prior year, abs and %, threshold flag; FCS-01 forecast generated = actual YTD + revised remainder; STD-02/03 flexible budget + spending variance run; CAP-04 three-way capacity reconciliation; ALC-06 GPK dual-rate closing option (planned usage for fixed, actual x standard rate for variable) extending the closing from `cmc_budget`; INT-07 version currency + fixed rate; wave B: FCS-04 monthly cash forecast from budget x payment terms | e-mail alerts, goal-seek iteration, budget import from EE, multi-step approval |
| WP-03 | STD-01 standard card per product per standard version (MP from BoM x planned price, MOD routing time x center rate, OH from planned rates; multi-level roll-up; type basic/ideal/attainable); STD-04 MO variances: material price/qty, labour rate/efficiency, OH spending/efficiency/volume, mix/yield; FC-08 over/under-absorbed per center per period; FC-09 push to `standard_price` via core revaluation from an approved version + card-vs-valuation reconciliation | daily/weekly team variances, PPV with TCO (TCO is WP-09), CMC-generated WIP/FG entries |
| WP-04 | FC-04 actual prime cost from cost-object-stamped MP/MOD lines (option standard vs actual); FC-05 produced qty from facts, unit cost, homogeneous output coefficient, Cu = v + CF/Q curve; driver qty prefilled from facts; FC-10 variable vs full view from line behavior/cost functions; FC-11 cost-plus price + bid check; CAP-03 practical-capacity denominator with idle cost kept out of products; publish unit costs (AD-108) | writing pricelists |
| WP-05 | FC-06 job cost sheet per project (direct costs by cost object, hours x resource rate, applied overhead = predetermined rate x base, WIP of open jobs, close job); CEN-04 hourly resource rates computed (dept expense / net hours) and published | WIP->FG postings, revenue recognition, billing |
| WP-06 | FC-07 process run per department/period: quantity schedule from MRP, WIP % typed, EU weighted average and FIFO, normal/abnormal spoilage from scrap, transferred-in chaining, control total, cost-of-production report | multiple material-addition points beyond per-element % |
| WP-07 | ABC-01 activity tree process>activity>task + owner; ABC-02 resources = center x element pools reconciled to in-scope cost base; ABC-03 resource-driver % matrix (100% check); ABC-04 rates on practical capacity, bill of activities, unit cost (published); ABC-05 hierarchy level + level-matching driver, batch drivers; ABC-06 cost-object types via AD-106 + CO->CO; ABC-07 direct trace; ABC-08 driver type/level/source/weighted index, fan-out via CO->CO profiles; ABC-09 acyclic network R->A->A->CO->CO, conservation, drill-back (cycles fail loud); ABC-10 sustaining object + optional absorption, idle and SG&A burden, F/V; ABC-11 ABC vs traditional from rates; INT-02 feeders | cyclic activity networks (reciprocal ABC) |
| WP-08 | ABM-01 process roll-up + activity x department matrix; ABM-02 attributes roll-up, Pareto (graph), quadrant as pivot; ABM-03 measures per activity + trend; ABM-04 initiatives with savings/one-time cost/payback/freed resources; ABM-05 unit activity cost benchmark across companies/periods; COQ-01 COQ attribute + trend + CoPQ = defect units x product ABC unit cost | bubble chart widget, value multipliers |
| WP-09 | PRF-01 layered margins by sustaining level from attributes, reconciled to P&L (unassigned row); PRF-02 customer P&L incl. capital charge (rate x AR balance); PRF-03 whale curve + quadrants (graph/pivot on ranked facts); PRF-04 price waterfall with mapped elements + simple surcharge/min-order what-if; PRF-05 supplier TCO from vendor bills + procurement activity costs; PRF-06 fact pivot + drill-down; DC-02 generate direct-costing statement from facts; DIF-05 below-floor list; ALC-07 unit service cost, criteria (market/marginal/full/cost-plus/negotiated/ABC-level), chargeback document; FIN-12 SBU reclassification | commission, list-price history beyond pricelist, menu pricing optimisation |
| WP-10 | DC-03 single-constraint mix ranking + TOC throughput; DC-05 tax gross-up + margin-% target; DC-08 lever table, A/B structures, iso-profit solve; DC-09 volume mix, Q* per product; DIF-01 persisted decision with lines (replaces the transient wizard, keeps its 3 types); DIF-03 indifference Q* + hidden activity costs from rates; DIF-04 quote/new-product costing from rates vs target margin; DIF-06 NPV/IRR/payback/ROI/CBR with inflation and tax (pure helpers, AD-119) | multi-constraint LP solver |
| WP-11 | FCS-02 copy baseline version + side-by-side delta P&L; RSK-01 Monte Carlo/LHS (normal, triangular, uniform) on version inputs through evaluator hooks registered in `cmc_base`, statistics/percentiles/P(profit>0); RSK-02 tornado +-10% + rank correlation with significance thresholds; INT-04 assumption register (base) + child->parent version consolidation | histograms as stored samples, correlated input sampling |
| WP-12 | BUD-06 reverse ABC from forecast volume (demand -> supply by spending profile -> shortage/excess, FTE -> center budget -> rates, extrapolation warning); FCS-03 driver what-if; CAP-05 time states + responsibility of unused cost + idle trend; CAP-06 spending profiles (flexible/committed/step, unit size), first constraining resource; STD-05 actual vs ABC vs ABB | variability scale UI beyond a selection, bottleneck scheduling (APS is native MRP) |
| WP-13 | FIN-01..11 per AD-116 (schemes, overrides, reduced mode, choices register, VA/cost-of-sales/contribution P&L, >=3-year series with common-size/YoY, imported sector benchmark, liquidity/solidity/profitability/DuPont/leverage ratios, functional + OIC 10 indirect cash flow, crisis indicators, WACC/EVA with typed inputs) | IFRS/consolidation (OOS), XBRL import, external data feeds |
| WP-14 | KPI-01 library (source: measure / ratio / variance / engine output via hook, target per version, thresholds, traffic light, trend); KPI-02 non-financial measures from feeders (scrap, yield, cycle time, OTD from stock moves); KPI-03 BSC perspectives, weights, cascade, links to activities; KPI-04 monthly management pack PDF | spreadsheet dashboards, real-time push |
| WP-15 | PRJ-01 estimate on tasks (WBS), rate cards from AD-108, PERT/three-point + accuracy band; PRJ-02 baseline version time-phased by month + risk register EMV + contingency/management reserve; PRJ-03 planned vs actual per period, control limits + rule of seven, change requests + re-baseline (version copy); PRJ-04 EVM indices/forecasts + S-curve, EV from task progress | agile sizing, contract/payment types (OOS) |
| WP-16 | TGT-01 allowable cost, gap, deployment to typed components/functions, supplier target price, kaizen target; TGT-02 feature costing = design variables x rates + guidelines; LCC-01 LC model (phases, horizon, non-recurring separate, committed vs incurred), LC unit cost recomputed on residuals; LCC-02 cost view and NPV view from the same driver base | BoM-driven component deployment, environmental/liability EMV extensions |

## AD-119 - Shared pure maths in `cmc_utils`, stdlib only, added with the first consumer

- **Decision**: NPV/IRR/payback/discounting with inflation (WP-10, reused by WP-08/12/16), PERT/three-point and EMV (WP-15), OLS/high-low (WP-01), equivalent units (WP-06), sampling/LHS/rank correlation (WP-11) live as pure functions in `cmc_base/models/cmc_utils.py` (or a sibling pure module), Python stdlib only (`math`, `random`, `statistics`) - no `numpy`/`scipy` requirement. Each helper is added by the first WP that needs it, with unit tests; later WPs reuse, never copy.
- **Motivation**: 少 - no new Python dependency on client servers; one formula, one place.
- **Traceability**: CLS-02, DIF-06, ABM-04, PRJ-01, PRJ-02, RSK-01, RSK-02, LCC-02, FC-07.

---

## Constraints for `odoo-dev-lead` (checklist per WP)

1. Put the code only in the module fixed by AD-101; add only the depends of AD-102.
2. Reuse A0 foundations (version, month grain, cost object, measures, rates, attributes): never add a parallel header, period, object key, driver quantity or rate table in a WP module.
3. Every engine = `cmc.run.mixin` run (AD-113); live comparisons = SQL views (AD-114).
4. Read costs only through `_cmc_cost_groups`/the cost-object reader, revenue and GL only through the `cmc_account` readers (AD-111); natural sign stored (AD-112).
5. Aggregate unbounded tables at the fact grain with `read_group`/`SQL` (AD-114).
6. Company field + `parent_of` rule + two-company tests on every model; rules not `noupdate`; sudo only for aggregate reads with `# why` (AD-115).
7. Stay inside the "Must" column of AD-118; anything in "Skip" needs a new AD.
8. No EE/OCA dependency (AD-103). Verify every v19 API on source/context7 (stock valuation in particular).

## Traceability

Capability ids per AD are listed in each record; WP <-> capability mapping per AD-118 (102 gaps; moves:
FCS-03 -> WP-12, FCS-04 -> WP-02, FIN-12 -> WP-09). No `TS-NNN`/`RF-AREA-NNN` exist yet for this
programme: the dev-lead/tech-specs pass assigns them per WP and cites these AD ids. `T<n>`/`HT<n>`:
to be assigned by the orchestrator.

## Consolidation backlog (orchestrator, 2026-10-09)
- CB-1: `cmc.run.result.mixin.write` raises UserError before the ACL check; WP-04 (cmc.costing.result) and WP-05 (cmc.job.sheet/.labour) added per-model `check_access("write")` overrides. Move the ACL check into the mixin and drop the overrides.
- CB-2: `_cmc_record_usage` does not de-duplicate origins within one call (WP-05 de-duplicates by hand).
- CB-3: `account.analytic.line` create/write overrides now in 4 modules; allocation lock called twice (cmc_base + cmc_hr_timesheet) — candidate single guard entry point (ronin WP-01 watch-point).
