# Release notes — gap programme vs management-accounting manuals (2026-10)

Branch `feat/gap-manuali` (integration of feat/gap-*). Analysis: `docs/analysis/gap-manuali-2026-10/`
(11 manuals → 136 capabilities; register `10_gap_register.md`). Architecture: `docs/superpowers/plans/2026-10-09-cmc-gap-architecture.md`
(AD-101..AD-119, consolidation backlog CB-1..CB-8). Every work package went through red tests → dev → QA → rōnin → fixes.

## Coverage
Before: 24/126 capabilities covered (19%). After: 125/126 covered + KPI-02 closed by `cmc_kpi_mrp` (see register §4/§5).

## New modules (CE, no Enterprise/OCA dependency)
| Module | Covers |
|---|---|
| cmc_budget | master/flexible budget, versions, rolling forecast, BvA view, spending/efficiency/volume/capacity variances, GPK dual-rate closing, cash forecast |
| cmc_fin_analysis | BS financial/functional reclassification (Italian chart rules), P&L value added / cost of sales / contribution, ratio pack, DuPont, leverage, cash-flow statements, crisis & data-quality indicators, EVA/MVA, multi-year, criteria-change register, PDF |
| cmc_standard_cost | standard prices, predetermined rate table, BoM cost cards with roll-up, MO variances (price/qty/mix/yield, labour, overhead), over/under absorption, push + reconciliation |
| cmc_job_costing | job cost sheets (job-to-date + period), resource-center rates, WIP/completed, under/over-applied overhead; EVM (PERT, baselines, risk reserves, change requests, indices, S-curve) |
| cmc_process_costing | process runs per department, EU weighted average/FIFO, spoilage, transferred-in chaining, cost of production report |
| cmc_profitability | profit facts, layered margins, economic profit, whale curve, quadrants, price waterfall, supplier TCO, price floor, SBU, transfer-pricing chargebacks |
| cmc_abb | activity-based budgeting, spending profiles, capacity states, bottleneck, ABC variance three-way, driver what-if |
| cmc_target_lcc | target costing, kaizen, feature costing, life-cycle costing (NPV, liabilities EMV, baseline delta) |
| cmc_scenario | scenario copy + delta P&L, Monte Carlo/LHS, tornado, Spearman, version consolidation |
| cmc_kpi | KPI library, targets, monthly KPI run with snapshot pack, OTD/scrap/yield, BSC scorecards, management pack PDF |
| cmc_kpi_mrp (auto_install) | cycle time, MO/delivery lead time, PPM, FPY, MCE |

## Extended modules
cmc_base (plan versions, cost objects, measure catalogue + feeders, rate registry, run usage, attributes, cost functions, hierarchy, responsibility centers + responsible group, allocation runs, step-down order, capacity levels, data checks, relocation predicate, run lock CB-6, output guard CB-7, stats/finance helpers);
cmc_account (GL/revenue readers, COGE↔COAN reconciliation); cmc_abc (v2 multistage engine, ABM, COQ); cmc_full_costing (actual prime cost, unit costs, cost-plus, practical capacity);
cmc_direct_costing (decisions, quotes, mix, investment, CVR levers); cmc_mrp / cmc_hr_timesheet (cost-object stamping, feeders, backfill migrations).

## Upgrade (verified on a copy of cmc_all, 19.0.1.0.0 → current: exit 0)
1. Copy first: `createdb -T <db> <db>_upg`.
2. `odoo-bin -d <db>_upg --addons-path=odoo/addons,enterprise,<repo> -u cmc_base,cmc_full_costing,cmc_direct_costing,cmc_account,cmc_hr_timesheet,cmc_mrp,cmc_abc,cmc_joint_costs,cmc_tdabc --stop-after-init`
3. `-i cmc_suite` to install the new modules (or install only the ones needed).
4. Reset + recompute concluded runs flagged by the migrations (see RELEASE_NOTES_refactor-2026-10 §9) and the v1 ABC runs (v2 engine, results identical on cmc_all).
5. Load Italian: `-u <modules> --load-language=it_IT` (or `i18n import -w`).

## Behaviour changes to know
- Computed runs are locked (CB-6): only `name` (and budget alert threshold) editable after compute.
- Relocation lines (allocations, chargebacks) are flagged and locked; DB CHECK link ⇒ flag.
- Produced-qty feeder excludes scrap moves; KPIs/costs refreshed from feeders at compute.
- `name` is no longer an input of runs.

## Documented limits
Cash forecast net of VAT; GPK dual-rate only for the direct method; process-costing report stores costs positive; transfer-pricing criteria live inside chargeback runs; `cvr` scenario evaluator not versioned (comparison/consolidation refuse it); G-DATA (AD-117) not measured on a client production db; FIN-09 no OIC 10 direct method; `ruff format` not applied repo-wide (lint `ruff check` E9/F/B clean).

## Technical debt
CB-8: nine runs still reset outputs by hand (no draft guard) — see architecture doc.
