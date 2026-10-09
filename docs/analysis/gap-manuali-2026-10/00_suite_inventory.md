# CMC suite (custom/cmc_suite01) - functional feature inventory

Paths are relative to `/Volumes/WDEXSSD/2ST/05_DEV/odoo19/custom/cmc_suite01/`. Odoo 19, LGPL-3 (except cmc_timesheet_grid OPL-1). Source read: all __manifest__, models, views, reports, data, security, README, RELEASE_NOTES, plans. Tests not read in detail. `.worktrees/refactor-2026-10` is a duplicate checkout of the same code (ignored).

## 0. Architecture in one page
- Cost base = native `account.analytic.line` (negative amount = cost, AD-005) extended with CMC fields (cmc_base/models/account_analytic_line.py:24-89). Cost centers = native `account.analytic.account` extended (cmc_base/models/account_analytic_account.py:12-38).
- Pipeline (cockpit BASE_STEPS, cmc_base/models/cmc_cockpit.py:12-22): Cost Centers -> Cost Entry -> Inter-Center Services -> Closing (ribaltamento). Then PARALLEL methodologies (METHOD_STEPS :27-38): Full Costing, Direct Costing/CVP, ABC, Joint, TDABC.
- Every "run" model shares `cmc.run.mixin` lifecycle (cmc_base/models/cmc_run_mixin.py:12-104): draft -> Compute -> done (locked) -> Reset to Draft (deletes results). States: only `draft`/`done` (:29-35). Result rows writable only by engine (`cmc.run.result.mixin` :168+). Input lines locked when done (`cmc.run.line.mixin`).
- Shared maths helpers: `largest_remainder`, `spread`, `qty_rounding` (cmc_base/models/cmc_utils.py:4-36): all allocations conserve the total to the cent.
- Reclassified ("riclassificato") transposed report contract `cmc.reclass.mixin` (cmc_base/models/cmc_reclass_mixin.py:9-66) -> OWL widget (cmc_base/static/src/reclass/) + QWeb PDF `cmc_base.report_reclass` (cmc_base/report/report_reclass.xml). Implemented by: full costing, direct costing, ABC, TDABC. NOT by closing, joint, CVR.
- Security groups: `group_cmc_user` (Controlling User), `group_cmc_manager` (Controlling Manager) (cmc_base/security/cmc_security.xml:15-24); global multi-company `ir.rule` (`company_id parent_of company_ids`) on every model (:30-91).
- Language: UI strings English with Italian i18n; vocabulary Italian (primo/industriale/complessivo/economico-tecnico, ribaltamento, co.an./co.ge., consuntivo/simulazione).

---
## 1. cmc_base (19.0.1.2.0) - core
depends: analytic, product. Menu root "Cost Management" (cmc_base/views/cmc_menus.xml:3).

### Models
| Model | Meaning / key fields | Ref |
|---|---|---|
| `account.analytic.account` (ext.) | `cmc_center_type` Selection: **auxiliary / intermediate ("Productive - Intermediate") / final ("Final / Common")**; `cmc_parent_id` / `cmc_child_ids` (hierarchy, cycle check); `cmc_is_period` (commercial/admin period center, outside closing; cannot also have a type) | account_analytic_account.py:12-56, 87-106 |
| `account.analytic.line` (ext.) | `cmc_behavior` **fixed / variable / semi_variable**; `cmc_directness` **direct / indirect** (default indirect); `cmc_cost_element_id` (nature); `cmc_driver_id`; `cmc_center_id` (computed, plan-agnostic convenience); `cmc_cost_amount` (positive view of cost); `cmc_excluded` (twin excluded from cost base, system-only); `cmc_relevance_min/max` (relevant range, constraint only, not used by any engine); `cmc_cost_object_ref` Reference (product, product.template, sale.order.line, project.project, cost center, customer) ; `cmc_cost_traceability` special/common relative to a cost object via context key `cmc_cost_object` | account_analytic_line.py:24-89, 128-138, 174-204 |
| `cmc.cost.element` | Cost nature taxonomy; `cmc_kind` **real / figurative**; seeded MP (Materie prime), MOD (Manodopera diretta), RIB (Ribaltamento centri ausiliari) | cmc_cost_analysis.py:4-30; data/cmc_data.xml |
| `cmc.cost.driver` | Allocation base (name, code, UoM) | cmc_cost_driver.py:4-23 |
| `cmc.allocation` + `.line` | Manual one-shot spread of an amount from a "common cost center" to target centers by driver quantity (largest remainder). Standalone: NOT consumed by closing/costing | cmc_allocation.py:8-96 |
| `cmc.center.service` | Inter-center service matrix provider -> receiver, driver, units of service (provider must be auxiliary/intermediate, receiver a typed non-period center; no self-service) | cmc_center_service.py:5-80 |
| `cmc.closing.run` (+ `.line`, `cmc.cost.analysis.line`) | **Closing / ribaltamento** run for a period | cmc_closing.py:32-363 |
| `cmc.capacity.analysis` | Per center + period: theoretical / practical / actual capacity -> fixed cost, fixed cost per practical unit, imputed fixed cost, **unused capacity cost**, utilisation % | cmc_capacity.py:7-144 |
| `cmc.cockpit` (transient) | JSON payload for OWL cockpit | cmc_cockpit.py:43-131 |

### Closing / ribaltamento engine (cmc_closing.py)
- Input: all centers with a type in company; initial cost per center = sum of cost lines in period, all behaviors (`_initial_costs` :71). Service proportions from `cmc.center.service` quantities (`_proportion_maps` :76-106).
- `method` Selection (ALL values): **direct** ("Direct"), **step_down** ("Step-down"), **reciprocal** ("Reciprocal") (:46-54).
  - direct: auxiliary/intermediate cost allocated only to FINAL centers (props over final receivers), ignores services among auxiliaries (:226-270; hint :242).
  - step_down: providers ordered by number of other to-close centers served (desc, then id), automatic ordering, no user-defined sequence (:217-224); each provider spreads over not-yet-closed receivers (:245-252).
  - reciprocal: solves linear system (I - A^T)S = initial by Gaussian elimination (`_solve_linear` :13-29, `_run_reciprocal` :272-335); errors on singular system, on cost without service, on no path to a final center.
- Outputs `cmc.closing.line`: cost_initial, cost_received, cost_distributed, cost_residual per center (:338-363); `cmc.cost.analysis.line` matrix center x element, states pre/post closing, reallocated cost booked under element RIB; conservation guard pre == post (:165-215).
- Errors fail loud: "cannot be closed: no allocation base" (:254).
- Period mandatory (date_from/date_to), dates check (:108-114).

### Capacity / unused capacity (cmc_capacity.py)
- fixed_cost = sum of lines with `cmc_behavior=fixed` on center in period (`_fixed_costs` :75-89); coeff = fixed/practical; imputed = coeff*actual; unused = fixed - imputed (:98-121); utilisation = actual/practical*100. Auto-resynced when analytic lines change (account_analytic_line.py:221-319). Onchange warning when actual > practical (:123-135). Button Recompute. Constraints: practical <= theoretical, non-negative, unique per center+period.

### Menus / actions / reports (cmc_base)
- Operations > Cost Base: Cost Centers, Cost Allocations, Inter-Center Services, Cost Entries (cmc_analytic_line_menu.xml:62), Closing Runs (cmc_closing_views.xml:75). Operations > Analysis & Control: Capacity Analysis (cmc_capacity_views.xml:87), Cost Analysis Matrix (pivot center x element, filters Before/After closing; cmc_reporting_views.xml:27-83).
- Dashboard menu: "Cost per Center" (pivot/graph of closing residual, :121), "Unused Capacity" (graph bar/pivot/list, :126). Cockpit client action "Cockpit" (cmc_cockpit_views.xml) = OWL pipeline + KPIs (centers, booked costs, computations, costed products) + doughnut of cost by element (cmc_cockpit.py:107-131).
- Configuration > Master Data: Cost Drivers, Cost Elements (cmc_reporting_views.xml:18-25).
- Buttons: Compute / Reset to Draft (closing form cmc_closing_views.xml:25-27), Recompute (capacity :29).
- PDF report "Cost Closing Report" on closing run (cmc_base/views/report_consuntivo.xml:3-12): per-center initial/received/distributed/residual.
- No Excel/CSV export beyond Odoo's standard list/pivot export.

### Limits / notes
- cmc_parent_id hierarchy is stored but "the suite never queries the CMC hierarchy" (account_analytic_account.py:7-10): no roll-up of centers by parent.
- `cmc_relevance_min/max`, `cmc_driver_id` on the line, `cmc_behavior=semi_variable` are stored but not split/used by any engine (semi-variable is treated as neither fixed nor variable by capacity (fixed only) and by CVR (fixed only)).
- `cmc.allocation` is isolated (not used by closing).
- Closing reads ALL cost lines of centers incl. unclassified; classification not required to close (cmc_ensure_classified exists, :206, but is not called by engines).

---
## 2. cmc_account (19.0.1.3.0) - accounting bridge
depends cmc_base, account.
- `account.account.cmc_cost_element_id`: map financial account -> cost element (account_account.py:4-15).
- `cmc.classification.rule`: ordered (sequence) auto-classification rules; criteria (AND, blank = wildcard): financial account, account code prefix, product category, partner, cost center; assignments: behavior (fixed/variable/semi_variable), directness (direct/indirect), cost element; fill-only, first matching rule wins (cmc_classification_rule.py:5-100; engine account_analytic_line.py:241-334).
- `cmc.manual.classification`: durable manual override per (journal item, center), survives draft/re-post (cmc_manual_classification.py:4-36).
- Analytic line ext.: `cmc_classified_manually`, `cmc_bridge_source`; classification gate lets a CMC user write only classification fields on foreign lines (account_analytic_line.py:115-140); bridge-profile hook `_cmc_bridge_profiles()` (:223-239) used by timesheet and MRP bridges.
- UI: Configuration > Integration > Classification Rules; "Unclassified Cost Lines" queue; server action "Re-apply Classification Rules" (cmc_exception_views.xml:28-57).
- Limits: no ageing/approval workflow; no budget; classification only fills behavior/directness/element.

## 3. cmc_hr_timesheet (19.0.1.2.0) - MOD from timesheets
- Profile `timesheet_mod`: timesheet line with project+employee+hours on a FINAL center -> element MOD, behavior variable, directness direct (account_analytic_line.py:24-50). Hours on auxiliary/period centers stay indirect. Bridge withdraws values when line stops matching.
- Limit: cost comes from the timesheet line amount (employee cost per Odoo); no hourly-rate/standard-rate layer.

## 4. cmc_timesheet_grid (19.0.1.0.0, Enterprise OPL-1, auto_install)
- Lets controllers classify validated timesheets (bypass of `_check_can_write` under CMC_GATE sentinel; models/account_analytic_line.py:6-15).

## 5. cmc_mrp (19.0.1.2.0) - prime cost from MRP
depends cmc_account, mrp_account.
- `mrp.production.cmc_center_id`: center absorbing MO material cost (must be on root plan and same company) (mrp_production.py:14-60).
- On MO done: books MP lines per consumed raw move at stock valuation value (SVL), element MP, variable, direct; adopts core-created analytic line instead of double booking; idempotent via `cmc_mrp_move_id` (mrp_production.py:92-201; account_analytic_line.py:8-20).
- Labour: profile `mrp_labour` (category manufacturing_order and UoM hour) -> MOD variable direct (account_analytic_line.py:64-97); the labour journal-entry "twin" lines are excluded from cost base (`cmc_excluded`, mrp_production.py:67-90, account_move_line.py).
- UI: MO form field (views/mrp_production_views.xml).
- Limits: no standard-vs-actual MO variance, no WIP/overhead absorption, no yield/scrap analysis.

---
## 6. cmc_full_costing (19.0.1.2.0) - full product cost
- `cmc.costing.run` (+ `.line` driver consumption, `.result`, `.quota`, `.config`) cmc_costing.py:10-478. Depends on a computed closing run; period must lie in closing's period (:61-84).
- `base` Selection: **single** ("Single base", plant-wide driver `single_driver_id`: all final-center cost pooled over total driver qty per product) / **multiple** ("Multiple base": each final center spread by its own driver consumption) (:32-36, `_center_map` :179-214).
- Four configuration levels (`level` Selection, :466-474): **primo** (Prime Cost) = product `standard_price` (company-specific) (:136-140); **industriale** = primo + indirect quota from closing residual of final centers; **complessivo** = + commercial/administrative period pool (centers flagged `cmc_is_period`) spread by driver units (:301-314); **economico_tecnico** = + figurative costs (:316-343).
- Result per product: cost_primo, cost_indiretto, cost_industriale, cost_comm_admin, cost_complessivo, cost_figurativo, cost_economico_tecnico, standard_price_ref (stock valuation per unit if available), std_price_delta (industrial minus std; indicative) (:399-428); quotas per center (coefficient, driver qty, amount); config levels cumulative + increment.
- `cmc.figurative.cost`: figurative cost record, `method` **lump / interest** (interest = capital x annual rate), dated validity, direct to product or indirect (spread by units) (cmc_figurative.py:9-95).
- UI: Operations > Full Costing > Costing Runs (cmc_costing_views.xml:162-170), Figurative Costs (cmc_figurative_views.xml:62); pivot of indirect quotas (:143); Compute / Reset buttons; reclassified PDF "Full cost (reclassified)" (report_actions.xml:3).
- Guards: cost without allocation base fails loud (:201-213); period/figurative pool without driver units fails (:240-256). Closing reset blocked while costing computed (cmc_closing.py:9-36).
- Limits (explicit/inferred): prime cost is **standard price**, NOT actual MP/MOD lines from the cost base; "per-unit inventory reconciliation requires produced quantity (not modelled)" (:421-426); product-level driver quantities are typed by hand; no produced quantities/volumes; fixed/variable split not used.

## 7. cmc_direct_costing (19.0.1.1.0) - contribution margin, CVP, differential
- `cmc.direct.costing` + `.line`: per product/line **revenue, variable cost, specific fixed cost** (all typed, free-text name, not linked to products or ledger) -> gross MC (rev - var), semigross MC (gross - specific fixed), MC %; header common fixed cost -> operating result (cmc_direct_costing.py:4-121). `mode` Selection: **simple** ("Simple (1 margin)") / **evoluto** ("Evolved (2 margins)") (:18-21). So 3 levels max: gross MC, semigross MC, operating result. Reclassified contribution-margin income statement + PDF (:48-87).
- `cmc.cvr.analysis` (+ `.line` mix, `.curve`): cmc_cvr.py:17-279.
  - `mode` Selection: **consuntivo** ("Actual (derived)": fixed cost derived from center's `fixed` cost lines in period via `_cmc_cost_groups`) / **simulazione** ("Simulation (override)": CF typed) (:42-45, `_derive_fixed_cost` :99-114). Price/variable cost per unit and volume always manual.
  - Outputs (stored): unit MC (p-v), MC ratio, has_breakeven, **break-even qty** Q* = CF/(p-v), **break-even revenue** = CF/ratio, **target-profit qty** (Uo+CF)/MC, **margin of safety** (value and %), **operating leverage** LO = MC_total/(MC_total-CF) (:62-87, `_cvp_figures` :136-183).
  - Multi-product mode (`is_multiproduct`, lines qty/price/var cost): weighted MC ratio -> break-even revenue; no Q* per product (:142-156).
  - Break-even chart: 4-point curve (0, BEP, 1.5xBEP, 2xBEP) as line graph revenue vs total cost (:216-241; view cmc_cvr_views.xml:22-30). Button Recompute (:185).
- `cmc.differential.analysis` (TransientModel, not persisted): `decision_type` Selection: **make_or_buy**, **drop_line** ("Eliminate Product Line"), **replace** ("Replace Machine"); computes differential and verdict (Buy/Make, Keep/Eliminate line, Replace/Keep machine, Indifferent) (cmc_differential.py:4-71). Inputs typed (avoidable make cost, buy price, opportunity cost; lost CM vs avoidable fixed; keep vs replace relevant cost). Formulas are a single-period comparison, no discounting/NPV.
- UI: Operations > Direct Costing: Direct Costing (MC), CVR / Break-even, Differential Analysis (views). Report "Reclassified (contribution margin)".
- Limits: no link to sales/invoice data; margins by product/line require manual entry; no multi-level by segment/customer hierarchy beyond free-text lines; no cost-behavior split from cost base into variable cost; CVP assumes single price/linear.

## 8. cmc_abc (19.0.1.1.0) - Activity-Based Costing (two stage)
- `cmc.abc.activity` (code unique per company, default driver) (cmc_abc.py:16-36). Analytic line field `cmc_activity_id` tags resource cost to an activity (account_analytic_line.py:7-13).
- `cmc.abc.run` (+ `.line` input, `.result`, `.activity.result`): cmc_abc.py:39-259. Stage 1: pool per activity = tagged cost lines in period (company-wide, sudo; `_cmc_activity_pools` :15-30). Stage 2: pool spread to **products only** pro-rata `driver_qty` typed per (activity, product) line (UNIQUE run/activity/product) (:75-128). Unallocated pool (cost on activity with no driver line) frozen as explicit "unallocated" rows and shown in reclass + `unallocated_amount` (:111-119, :58-73).
- Outputs: ABC cost per product, per-activity snapshot; reclassified report (cost per activity x product, subtotal, unallocated) + PDF "ABC (reclassified)".
- UI: Operations > Activity-Based Costing > ABC Computation, ABC Activities (cmc_abc_views.xml:144-151).
- Limits: cost objects = products only (no customers/channels; TDABC has customers); no resource drivers (stage 1 is a tag, 1 line = 1 activity, no % split of a resource across activities); no activity hierarchy (unit/batch/product/facility) attributes; no value-added/non-value-added flag; no activity-based budget; no unit cost (cost / quantity produced) output; no margin per object.

## 9. cmc_tdabc (19.0.1.1.0) - Time-Driven ABC
- `cmc.tdabc.resource`: period cost, theoretical time (min), utilisation % (default 80) -> practical capacity, **capacity cost rate (cost per minute)** (cmc_tdabc.py:92-124).
- `cmc.tdabc.activity` + `.term`: base time + sum(coefficient x characteristic value) = **time equation** (structured, no eval) (:127-190). `cmc.tdabc.characteristic` ttype **bool / number** (:19-32); values on product template or partner (`cmc.tdabc.char.value`, product_template.py, res_partner.py).
- `cmc.tdabc.run` (+ `.line`, `.result`, `.capacity.result`): lines = activity x cost object (`cost_object_type` **product / customer**) x volume (typed, or `volume_derived` = count of tagged analytic lines per product/partner) (:239-440). Scenarios (`SCENARIOS`, :194): **manual** "Estimate (equation)" and **timesheet** "Actual (timesheet)" (flat minutes per unit = actual tagged hours*60 / total volume; analytic field `cmc_tdabc_activity_id`, account_analytic_line.py:4-32). Timesheet scenario only if tagged hours exist.
- Outputs: per scenario time absorbed and cost absorbed per object+activity (largest remainder); per resource: required time, unused time, **unused capacity cost** (exact residual cost - absorbed), capacity use %, over-capacity flag (:392-423). Reclassified report estimate vs actual, with unused capacity rows + PDF.
- Warnings text for activities excluded from a scenario (`scenario_warning`).
- UI: Operations > Time-Driven ABC: TDABC Computation, Resources, TDABC Activities, Characteristics, Characteristic Values (cmc_tdabc_views.xml:288-298).
- Limits: customers appear as cost objects (cost-to-serve of activities only); no revenue so no customer profitability/whale curve; resource cost typed (not pulled from ledger); time in minutes; no multiple-resource per activity; no activity-based budget.

## 10. cmc_joint_costs (19.0.1.1.0)
- `cmc.joint.run` (+ `.line`, `.result`) cmc_joint.py:8-247. Joint cost pool typed. `method` Selection: **physical** (Physical Units), **sales_value** (Sales Value at Split-off), **nrv** (Net Realizable Value), **constant_gm** (Constant Gross Margin) (:24-32). By-product flag: valued at NRV = max(qty x unit sales value - separable cost, 0) and netted off the pool before allocating (:48-81). Result per product: allocated joint cost, separable cost, total cost, unit cost (:167-178, 229-247). Fail-loud guards (net < 0, no main product, base <= 0, constant-GM negative share).
- UI: Operations > Joint Costs > Joint Cost Allocation. No report/pivot.
- Limits: joint cost pool, quantities and prices are typed; no link to MO, stock or sales; no further processing inventory valuation.

## 11. cmc_suite (19.0.1.0.0): meta-module (depends on all except timesheet_grid), application=True; no models.

---
## 12. Seeds / docs / tests
- Seed scripts for demo: scripts/cmc_seed_demo.py, cmc_seed_full.py, cmc_seed_tdabc.py, cmc_seed_year.py, cmc_seed_year_commesse.py (commesse = job orders: seeds only, no job-costing model).
- Docs: README.md, per-module README.md, docs/RELEASE_NOTES_refactor-2026-10.md (sign convention, mandatory closing period, manual classifications per center, MRP twin, bridge profiles, lifecycle, upgrade procedure), docs/superpowers/plans/ (master + block1). Tests: ~390 (README/plans).

---
## 13. TOPIC COVERAGE TABLE (YES / PARTIAL / NO)

| # | Topic | Verdict | Evidence |
|---|---|---|---|
| 1 | Cost centers & hierarchy | PARTIAL | Centers typed auxiliary/intermediate/final + period flag (account_analytic_account.py:12-38); `cmc_parent_id` tree exists with cycle check (:23-32, 87-106) but never used for roll-up ("suite never queries the CMC hierarchy", :7-10). Hierarchy of plan = native analytic plans only |
| 2 | Cost classification fixed/variable, direct/indirect, by nature | YES | cmc_behavior fixed/variable/semi_variable, cmc_directness, cmc.cost.element with real/figurative (account_analytic_line.py:24-75; cmc_cost_analysis.py:4-30); auto rules in cmc_account. Semi-variable never split |
| 3a | Primary->secondary allocation: direct | YES | closing method direct (cmc_closing.py:46-54, 226-270) |
| 3b | Step-down / sequential | YES (auto order) | `_ordered_providers` :217-224; order not user-definable |
| 3c | Reciprocal | YES | linear system solve :272-335 |
| 4 | Full costing | YES | cmc_full_costing, 4 levels, single/multiple base (cmc_costing.py:32-36, 466-474). Prime cost = standard_price (:136-140) |
| 5 | Direct costing / contribution margin multi-level | PARTIAL | gross + semigross MC + operating result, 2 levels (cmc_direct_costing.py:104-121), all inputs manual; no more levels (segment/customer/channel), not fed from ledger |
| 6a | CVP / break-even | YES | cmc_cvr.py:62-87, 136-183 |
| 6b | Margin of safety | YES | :79-84 |
| 6c | Operating leverage | YES | :85-87, 180-182 |
| 7 | Differential analysis / make-or-buy | YES (simple) | cmc_differential.py:13-71 (make_or_buy, drop_line, replace); transient, manual inputs, no NPV, not saved |
| 8 | Standard costs | PARTIAL | only product.standard_price used as prime cost + reference (cmc_costing.py:136-140, 345-358); no standard cost cards (qty/price standards), no standard setting/roll-up |
| 9a | Master budget | NO | no budget model (grep for budget: none in code) |
| 9b | Flexible budget | NO | none |
| 10 | Variance analysis (price/qty/efficiency/volume/mix/yield) | NO | none; only indicative `std_price_delta` (industrial cost - std price) in full costing (cmc_costing.py:421-426) |
| 11a | ABC resource drivers | NO | stage 1 is a tag on analytic line (cmc_abc/account_analytic_line.py:7-13); no resource->activity % |
| 11b | ABC activity drivers | YES | typed driver_qty per activity x product (cmc_abc.py:184-213) |
| 11c | Activity hierarchy (unit/batch/product/facility) | NO | none |
| 11d | Cost objects incl. customers/channels | PARTIAL | ABC: products only; TDABC: product + customer (cmc_tdabc.py:239+ cost_object_type); no channels; `cmc_cost_object_ref` can reference customer/project/sale line but is informational (account_analytic_line.py:76-81, 128-138) |
| 12 | TDABC (capacity cost rate, time equations) | YES | cmc_tdabc.py:92-190 |
| 13 | Unused capacity reporting | YES | cmc.capacity.analysis (cmc_capacity.py) + dashboard "Unused Capacity" + TDABC capacity result (cmc_tdabc.py:392-423) |
| 14 | Activity-based budgeting | NO | none |
| 15 | ABM (VA/NVA attributes, process view) | NO | none; activity has only name/code/driver |
| 16 | Customer profitability / cost-to-serve / whale curve | PARTIAL (cost-to-serve only) | TDABC customer cost objects absorb activity cost (cmc_tdabc.py cost_object_type customer); no revenue, no margin, no cumulative profit curve |
| 17 | Joint / by-product costing (physical, sales value, NRV) | YES | cmc_joint.py:24-32 (+ constant gross margin); by-product NRV netting :56-60 |
| 18 | Target costing | NO | none |
| 19 | Life cycle costing | NO | none |
| 20 | Cost of quality | NO | none |
| 21 | Project cost management / earned value (PV, EV, AC, CPI, SPI, EAC) | NO | project.project only selectable as informational cost object reference (account_analytic_line.py:134); no EVM |
| 22 | KPI / balanced scorecard / dashboards | PARTIAL | OWL cockpit with 4 KPIs + cost-by-element doughnut (cmc_cockpit.py:107-131), pivots/graphs (cost per center, unused capacity); no BSC, no KPI library |
| 23 | Financial statement analysis (reclassified BS/P&L, ratios, cash flow) | NO | "reclassified" reports are cost-statement layouts of each costing (cmc_reclass_mixin.py), not financial statements; no ratios |
| 24a | Responsibility accounting / profit centers | PARTIAL | center types + period centers; no profit-center P&L or manager responsibility link/revenue on centers |
| 24b | Transfer pricing | NO | none (inter-center services valued at cost, cmc_center_service.py) |
| 25 | Period comparisons actual vs budget vs previous year | NO | runs are per-period, no comparison view or budget; user can compare by opening two runs/pivot filters |
| 26 | Forecasting / rolling forecast | NO | none |
| 27 | What-if simulation | PARTIAL | CVR "simulazione" mode (cmc_cvr.py:42-45) and transient differential analysis; ABC/TDABC/costing inputs editable in draft runs; no scenario manager |
| 28a | Multi-company | YES | company_id on all models, global ir.rule parent_of (cmc_security.xml:30-91), `_check_company_auto`, company-scoped engines (`_cmc_cost_groups` company arg :19-36) |
| 28b | Multi-currency | PARTIAL | monetary fields use company currency; analytic `amount` is already company-currency; no conversion layer in CMC; cockpit formats by current company (cmc_cockpit.py:89-111) |

## 14. Other noteworthy functional capabilities not in the topic list
- Document-to-cost bridges: vendor bills (via account analytic lines + rules + account->element map), timesheets (MOD), MRP (MP + MOD), with twin exclusion and no double booking.
- Audit/lock lifecycle with deterministic, conserving rounding (largest remainder), reset semantics and dependency protection (closing <- costing).
- Unclassified-cost queue and rule re-application; manual override persistence.
- Migrations for sign flip / classification relocation documented in RELEASE_NOTES.
