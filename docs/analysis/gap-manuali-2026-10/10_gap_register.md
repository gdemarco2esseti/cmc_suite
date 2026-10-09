# 10 - CMC suite GAP REGISTER (consolidated)

Input: capability tables 01-08 (~530 rows from 11 books) deduplicated into **136 canonical capabilities**, checked against `00_suite_inventory.md` and the source in `custom/cmc_suite01/` (paths below relative to it).

Source file ids: **01** = Squaratti (SQ) / Bookboon process+ABC (BB) / Project Cost Mgmt (PCM); **02** = Cinquini Vol. I; **03** = Fazzini, Analisi di bilancio; **04** = Kaplan-Cooper, Cost & Effect; **05** = Cokins, Executive's Guide; **06** = Cokins MIW (MIW) + Frost (Frost); **07** = Turney, Common Cents; **08** = Emblemsvag, LCC using ABC + Monte Carlo.

Status rules (strict): **COVERED** = a user can do it in the UI and the engine computes it. **PARTIAL** = some of it computes, or the computation exists but runs on hand-typed inputs where the books need system data; the missing part is stated. **MISSING** = no model/engine. **OOS** = out of scope: not a software function of a management-control system, or better served by native Odoo/OCA. OOS items are listed but not counted in the gap.
Weight: **core** = at least 2 books treat it as essential, or it is standard controllo di gestione for Italian PMI; **advanced**; **niche**.

---

## 1. Register

### CEN - Cost centers and responsibility
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| CEN-01 | Define the cost-center master with typology (auxiliary / intermediate / final, productive / service / common, period centers) | n/a (master data) | 02 I.8, V.1.1, VI.1; 04 Ch3; 07 Ch5 | core | COVERED | `cmc_center_type` + `cmc_is_period` (cmc_base/models/account_analytic_account.py:12-56) | - |
| CEN-02 | Keep a center hierarchy (BU > department > sub-center / centro di prelievo) with roll-up and drill-down of actuals, costs and variances | node value = Σ children; no "summary departments" | 02 V.1.1-1.2; 06 Frost St7-8; 07 Ch5, Ch14; 08 Ch5 | core | PARTIAL | `cmc_parent_id` tree stored with a cycle check, but "the suite never queries the CMC hierarchy" (account_analytic_account.py:7-10). Missing: roll-up engine, a hierarchical pivot/report, BU drill-down | WP-01 |
| CEN-03 | Manage responsibility centers (cost / revenue / profit / investment) with an owner, a controllable / non-controllable flag per item and N:M mapping to cost centers. Report each manager's controllable costs; ROI for investment centers | ROI = result / invested capital of center | 02 I.8, V.1.1, VI.1; 04 Ch3; 05 Ch2 (primary vs secondary cost); 01 SQ "Errori" #3 | core | MISSING | Inventory §13 #24a: no owner, no revenue, no responsibility type; only 2 global groups (cmc_base/security/cmc_security.xml:15-24) | WP-01 |
| CEN-04 | Treat a person, machine or vehicle as a resource center with an hourly rate, and use that rate to value hours on jobs. Variant: hourly-rate method (dept expense / net working hours) | rate = (special costs + share of common costs) / hours provided; net hours = 2,080 - vacation/holidays/sick | 02 VI.1; 06 Frost St6; 01 PCM Ch3 (rate card) | advanced | PARTIAL | Rates exist but are not applied to hours: fixed cost per practical unit (cmc_capacity.py:98-121), TDABC cost/min (cmc_tdabc.py:92-124). Timesheet MOD uses the Odoo employee cost (cmc_hr_timesheet, inventory §3) | WP-05 |

### CLS - Cost classification
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| CLS-01 | Classify costs by nature (cost element, real vs figurative) and map GL accounts to elements | COGE account → element | 02 I.1-I.2, V.2; 05 Ch1; 07 Ch14 | core | COVERED | cmc_base/models/cmc_cost_analysis.py:4-30; cmc_account/models/account_account.py:4-15 | - |
| CLS-02 | Classify cost behaviour as fixed / variable / semi-variable / step, with its cost driver and relevant range. Split semi-variable into K + vQ from history and warn when a computation leaves the relevant range | C=K; C=vQ; C=K+vQ; step. High-low: v=(Cmax-Cmin)/(Qmax-Qmin), K=Cmin-v·Qmin; least squares Σy=nb+aΣx, Σxy=bΣx+aΣx² | 02 I.3, I.4, II.7; 04 Ch6-7; 05 Ch8; 06 Frost St6; 01 PCM Ch7 | core | PARTIAL | `cmc_behavior` + rules are stored (account_analytic_line.py:24-89). Semi-variable is never split; `cmc_relevance_min/max` and the line `cmc_driver_id` feed no engine. Missing: K/v split, step functions, high-low/OLS estimator, range warnings | WP-01 |
| CLS-03 | Classify costs as direct / indirect and as special / common relative to a chosen cost object | traceability depends on the object | 02 I.5-I.6; 01 PCM Ch7; 05 Ch6 | core | COVERED | `cmc_directness`, `cmc_cost_traceability` via `cmc_cost_object` context (account_analytic_line.py:128-138, 174-204) | - |
| CLS-04 | Flag items for decisions: relevant, avoidable (eliminabile), opportunity, sunk, committed / discretionary, controllable | Δ = only relevant items; avoidable fixed cost per line | 02 I.7, III.4.1; 01 PCM Ch7; 04 Ch7, Ch15; 05 Ch8 | core | PARTIAL | Avoidable and opportunity cost exist only as typed inputs of the transient differential wizard (cmc_differential.py:24-58). No flags on cost lines or direct-costing lines | WP-01 |
| CLS-05 | Auto-classify cost lines with ordered rules, keep manual overrides and an unclassified queue | first matching rule wins, fill-only | 02 I.1; 06 Frost St3, Appx A; 07 Ch13 | core | COVERED | cmc_account/models/cmc_classification_rule.py:5-100; cmc_manual_classification.py:4-36; cmc_exception_views.xml:28-57 | - |

### ALC - Allocation and closing (ribaltamento)
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| ALC-01 | Localize costs to centers directly or by a localization parameter (m², kWh, headcount, plant value) | share_c = amount × param_c / Σ param | 02 V.1.2; 04 Ch3; 06 Frost St6, St8 (occupancy) | core | PARTIAL | `cmc.allocation` spreads by driver with largest remainder (cmc_allocation.py:8-96), but it is standalone: its output is not booked into the cost base and closing/costing do not read it | WP-01 |
| ALC-02 | Build the Quadro Analisi Costi (elements × centers, before and after ribaltamento) | totals per center, rows for ribaltamento | 02 V.1.5 | core | COVERED | cmc_reporting_views.xml:27-83; cmc_closing.py:165-215 (RIB element, conservation guard) | - |
| ALC-03 | Close intermediate centers with the direct method. Choose a 2-phase or 3-phase scheme (structure centers onto productive centers, or kept as period costs) | aux cost spread only over final centers by service share | 02 V.1.3, VI.4.1; 04 Ch3 App | core | COVERED | cmc_closing.py:226-270. Scheme chosen by center typing (auxiliary vs `cmc_is_period`) | - |
| ALC-04 | Close with the step-down method in a controlled order (by share of service to other intermediates, or a user-defined sequence) | closed center receives nothing more; e.g. A→B: 20,000 × (1-300/800) to finals | 02 V.1.3; 04 Ch3 App | core | PARTIAL | Order is automatic: by the count of centers served, then id (`_ordered_providers`, cmc_closing.py:217-224). Missing: ordering by service share, manual sequence | WP-01 |
| ALC-05 | Close with the reciprocal method (simultaneous equations) | C_i = D_i + Σ_j q_ji·C_j, i.e. (I - Aᵀ)S = D | 02 V.1.3; 04 Ch3, Ch13; 05 Ch2 | core | COVERED | Gaussian elimination (cmc_closing.py:13-29, 272-335) | - |
| ALC-06 | Allocate support centers GPK-style: fixed cost on planned usage, variable cost on actual usage at the standard rate. Allow several pools per center (labour-h, machine-h) and two-tier rates | fixed charge = monthly fixed budget × planned_h_i/Σplanned_h; var rate = budget var / planned driver h (262,200/11,350=23.10); charge = rate × actual h; tier1 = committed/practical cap, tier2 = flexible/expected vol | 04 Ch3 App, Ch14 | advanced | MISSING | Closing uses actual service quantities only (cmc_closing.py:76-106); one driver per center in costing (cmc_costing.py:179-214) | WP-02 |
| ALC-07 | Price internal services and inter-unit transfers: unit service cost, chargeback by usage, transfer price criteria (market, marginal, full, cost-plus, negotiated, ABC-level based) | unit price = center cost / service units; unit+batch at std rates on actuals, sustaining annually on budget | 02 V.1.3; 03 12.7; 04 Ch12-13, Ch15; 05 Ch6 | advanced | MISSING | Inter-center services carry quantities only and are valued at cost inside the closing (cmc_center_service.py:5-80). No unit service cost, chargeback or transfer price | WP-09 |
| ALC-08 | Document each allocation base (criterion: cause-effect / benefit / fairness / ability to bear), its rationale and whether it is traced or allocated. Report the allocated share (guardrail below 10%) | % allocated = allocated cost / total overhead | 02 I.6 box; 01 BB 4.5; 06 MIW ch1; 07 Ch14; 08 Ch4-5 | advanced | PARTIAL | Driver = name/code/UoM only (cmc_cost_driver.py:4-23). Missing: criterion, rationale, traced/allocated flag, share report | WP-01 |

### FC - Full costing and product costing systems
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| FC-01 | Absorb overhead with a single plant-wide base (by value or by quantity) | coeff = indirect cost / Σ base (49,000/270,000 = 18.148% or 28.994 €/h); share = coeff × object base | 02 I.6, IV.2; 01 BB Ch1 | core | COVERED | `base=single`, `single_driver_id` (cmc_full_costing/models/cmc_costing.py:32-36, 179-214) | - |
| FC-02 | Absorb overhead with multiple bases (one per center/pool) | coeff_c = center cost / Σ base_c; product = Σ coeff_c × base_c,product | 02 IV.2, V.1.4-1.5; 01 BB Ch2-3 | core | COVERED | `base=multiple` (cmc_costing.py:179-214) | - |
| FC-03 | Produce the progressive cost configurations: primo → industriale → complessivo → economico-tecnico (with figurative costs) | primo + indirect ind. = fabbricazione; + comm./admin = complessivo; + figurativi (lump or capital × rate) = economico-tecnico | 02 V.2, I.2 | core | COVERED | `level` (cmc_costing.py:301-343, 466-474); cmc_figurative.py:9-95 | - |
| FC-04 | Compute prime cost from actual direct costs (MP + MOD + external processing) per product or job, not from standard price | Σ Q×P materials + hours × rate | 02 V.2, IV.2; 01 BB Ch1; 05 Ch2, Ch6 | core | PARTIAL | primo = product `standard_price` (cmc_costing.py:136-140). The MP/MOD lines booked by cmc_mrp / cmc_hr_timesheet are not consumed | WP-04 |
| FC-05 | Compute unit cost per product from produced quantities. Center coefficient per homogeneous output; driver quantities from MRP/routing; show the unit-cost instability curve | unit = total / produced qty; homogeneous output = Σ(unit time × units); coeff = center cost / output (100,239.43/4,620 = 21.6968 €/min); Cu = v + CF/Q | 02 V.1.4-1.5, I.4, VI.2.2; 01 SQ "Errori" #2; 01 BB Ch2; 06 Frost bg | core | PARTIAL | "requires produced quantity (not modelled)" (cmc_costing.py:421-426); driver qty typed by hand; no unit cost and no Cu curve | WP-04 |
| FC-06 | Run job-order costing (commessa): job cost sheet, WIP per job, transfer to FG, overhead applied to tasks | job = direct materials + DL h × rate + overhead (base × predetermined rate) | 01 BB 1.2-1.6; 02 IV.1-IV.2; 05 Ch6 | core | MISSING | Only demo seeds (scripts/cmc_seed_year_commesse.py). `cmc_cost_object_ref` to project is informational (account_analytic_line.py:76-81) | WP-05 |
| FC-07 | Run process costing with a cost-of-production report per department: quantity schedule, equivalent units, weighted average or FIFO, allocation to transferred-out and ending WIP with a control total, transferred-in chaining, normal vs abnormal spoilage | EU = completed + WIP_end × %; WA cost/EU = (BWIP + period cost)/EU per element; FIFO V = P + α·Rsf - β·Rsi; check transferred + WIP_end = total (8,012,403 + 1,457,097 = 9,469,500) | 01 BB Ch1-3; 02 VI.3, IV.1 | core | MISSING | none | WP-06 |
| FC-08 | Use predetermined (standard) overhead rates on actual bases, report over/under-absorbed overhead, and refresh rates only on permanent change (rate aging) | rate = budget pool / budget (or practical) base; applied = rate × actual base; over/under = actual - applied | 01 BB Ch1; 04 Ch14; 05 Ch3; 06 Frost St6-7 | core | MISSING | Every coefficient is actual-period (cmc_costing.py:179-214; cmc_abc.py:75-128) | WP-03 |
| FC-09 | Value inventory with a selectable configuration (primo / industriale; product vs period costs), push the cost back to Odoo (standard_price, rates), post costing entries (WIP/FG) and reconcile the managerial view with the GAAP view | Dr WIP / Cr RM, wages, overhead; transfer to FG | 02 V.2, IV.3, VI.4; 01 BB 3.2-3.4, 4.1-4.3; 04 Ch2, Ch14; 05 Ch1, Ch3; 07 Ch3 | core | PARTIAL | Only an indicative `std_price_delta` (cmc_costing.py:399-428) and a period/product split via `cmc_is_period`. No push to standard_price, no posting, no reconciliation | WP-03 |
| FC-10 | Produce a variable (marginal) product cost and a full-absorption product cost from the same model | variable rates only vs full rates; include/exclude sustaining, idle, figurative | 05 Ch3, Ch6; 04 Ch3 App; 06 Frost St6 | core | PARTIAL | Levels are cumulative full-cost only. The fixed/variable split on lines is not used by costing | WP-04 |
| FC-11 | Price with cost-plus and check bids against cost | price = full cost × (1 + mark-up); bid > ABC cost | 02 V.2; 07 Ch8 | core | MISSING | none | WP-04 |

### DC - Direct costing and CVP
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| DC-01 | Produce a simple and an evolved direct-costing statement (1st/2nd margin) and flag negative MC | MC = p - v; 1° MC = R - CV; 2° MC = 1° MC - specific fixed; result = Σ 2° MC - common fixed | 02 III.1-III.2, III.4.1 | core | COVERED | cmc_direct_costing.py:4-121 (`mode` simple/evoluto); `has_breakeven` (cmc_cvr.py:67) | - |
| DC-02 | Feed the margin statement from actual sales and the classified cost base, by product / line / customer / segment | R from invoice lines; CV from variable lines; CF specific from traced fixed lines | 01 SQ "5 segreti" #4-5; 02 VI.4; 03 4.2; 08 Ch4 | core | PARTIAL | Lines are free text typed by hand and not linked to product, sales or ledger (inventory §7) | WP-09 |
| DC-03 | Choose the product mix under a constraint and rank by margin per unit of the scarce factor | MC per constrained unit = MC_unit / factor per unit (200/2 = 100 vs 250/5 = 50); TOC T/CU = (price - material)/constraint min | 02 III.3; 05 Ch8; 06 Frost St8 | core | MISSING | none | WP-10 |
| DC-04 | Compute break-even in units and revenue, with chart and linear cost function | Q* = CF/(p-v); RT* = CF/(1-v/p); CT = CF + vQ | 02 II.1, I.4; 06 Frost St8; 03 13.5.1 | core | COVERED | cmc_cvr.py:62-87, 136-183, curve :216-241 | - |
| DC-05 | Compute the volume/revenue needed for a target profit, after tax or as a margin % | Qo = (Uo+CF)/(p-v); RTo = (Uo+CF)/(1-β); UL = Un/(1-t); Frost Q = FC/(SP - VC - PM·SP) | 02 II.2; 06 Frost St8 | core | PARTIAL | `target_qty` = (Uo+CF)/MC only (cmc_cvr.py:75-78). Missing: tax gross-up, margin-% target | WP-10 |
| DC-06 | Compute the margin of safety (and profit-volume chart) | MS% = (RT - RT*)/RT × 100 | 02 II.4 | core | COVERED | cmc_cvr.py:79-84 (no P/V chart, minor) | - |
| DC-07 | Compute the operating leverage | LO = MC/(MC - CF); ΔUO% = LO × ΔRT% | 02 II.5; 03 13.5.1 | core | COVERED | cmc_cvr.py:85-87, 180-182 | - |
| DC-08 | Run sensitivity on the 4 levers (p, Q, CF, v), compare cost structures A/B and solve iso-profit combinations | U = pQ - vQ - CF with ±% on one lever; e.g. +20% CF covered by +5% p or -10% v | 02 II.3, II.5; 03 13.5.1 | core | PARTIAL | You can change inputs and Recompute; `simulazione` overrides CF (cmc_cvr.py:42-45, 185). Missing: lever table, side-by-side structures, iso-profit solve | WP-10 |
| DC-09 | Run multi-product CVR with a constant mix (revenue mix or volume mix) and split break-even by product | RT* = CF/Σα_j(1 - v_j/p_j); Q* = CF/Σα_j·mc_j | 02 II.6 | core | PARTIAL | Weighted MC ratio → BEP revenue only (cmc_cvr.py:142-156). Missing: volume mix, Q* per product, multi-product chart | WP-10 |

### DIF - Decisions
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| DIF-01 | Run a generic, saved differential analysis (current vs alternative columns of relevant revenues and costs), including processing beyond split-off | Δ = (new revenues + avoided costs) - (lost revenues + new costs) | 02 III.4, IV.3.2 | core | PARTIAL | Transient model, 3 fixed decision types, nothing saved (cmc_differential.py:4-71) | WP-10 |
| DIF-02 | Decide whether to drop a product line | Δ = -1° MC + avoidable CF; drop if avoidable CF > 1° MC | 02 III.4.1; 04 Ch9; 05 Ch6 | core | COVERED | `drop_line` (cmc_differential.py:58) | - |
| DIF-03 | Decide make-or-buy on relevant costs, with the indifference quantity and hidden activity costs | relevant make = MP+MOD+variable CIP+avoidable CF; Q* = CFr/(Pe - v) | 01 PCM Ch9; 02 III.4.2; 05 Ch6; 07 Ch1, Ch8 | core | PARTIAL | `make_or_buy` unit comparison (cmc_differential.py:24, 51-71). Missing: Q*, ABC hidden costs | WP-10 |
| DIF-04 | Cost an order, quote or new product from activity rates and characteristics, and test it against the target margin | cost = Σ driver qty × activity rate (+ capital charge); accept if price - cost ≥ threshold | 04 Ch9, Ch15; 05 Ch4, Ch8; 06 Frost St5, St8; 07 Ch9 | core | MISSING | none | WP-10 |
| DIF-05 | List products priced below ABC or variable cost (price floor, reprice, minimum order charge) | floor = variable ABC cost if fixed costs are covered and capacity exists | 07 Ch8-9; 06 Frost St6; 02 V.2 box | core | MISSING | none | WP-09 |
| DIF-06 | Appraise investments and replacements over a horizon: NPV, IRR, payback, ROI, CBR, TCO, inflation (real/nominal), tax, discount-rate options, moving baseline | NPV = Σ CF_t/(1+r)^t - I; IRR: NPV=0; payback = I / annual net benefit; ROI = (gain - I)/I; FV = PV(1+r)^n | 01 PCM Ch2, Ch4; 02 III.4.3; 08 Ch5-6, Ch8 | core | PARTIAL | `replace` is a single-period comparison with no discounting (cmc_differential.py:13-71) | WP-10 |

### JNT - Joint costs
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| JNT-01 | Allocate joint costs (physical units, sales value at split-off, NRV, constant gross margin) and handle by-products at NRV | coeff = joint / Σ qty (1,400/9,000); share = joint × base_i/Σ; constant GM: share = R - separable - GM% × R | 02 IV.3.3; 08 Ch2 | advanced | COVERED | cmc_joint_costs/models/cmc_joint.py:24-81, 167-178 (inputs typed, no link to MO) | - |

### CAP - Capacity
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| CAP-01 | Manage five capacity levels per center (theoretical, practical, normal, budget, actual) with practical-capacity estimation rules | coeff per level = (CF+CV)/capacity (2.50; 2.63; 2.83; 2.91; 3.00); practical = theoretical × (1 - breaks/downtime) | 02 VI.2; 04 Ch7; 07 Ch6 | core | PARTIAL | Only theoretical/practical/actual (cmc_capacity.py:7-144); TDABC utilisation % (cmc_tdabc.py:92-124). Missing: normal and budget levels | WP-01 |
| CAP-02 | Compute and report the cost of unused capacity on practical capacity, with utilisation alerts | unused = fixed - fixed/practical × actual; resources supplied = used + unused | 02 VI.2.4; 04 Ch7; 05 Ch6; 06 Frost St6; 07 Ch6; 08 Ch2 | core | COVERED | cmc_capacity.py:98-135 + "Unused Capacity" dashboard; TDABC capacity result (cmc_tdabc.py:392-423) | - |
| CAP-03 | Charge products only for capacity used (rates on practical capacity), never re-absorb idle cost | product charge = used × rate_practical | 04 Ch7; 05 Ch6; 07 Ch2-3, Ch6; 01 BB 4.1 | core | PARTIAL | TDABC does this. Full costing and ABC spread the whole pool over actual drivers (cmc_costing.py:179-214; cmc_abc.py:75-128) | WP-04 |
| CAP-04 | Reconcile budget, assigned and actual expense into spending variance, unexpected utilisation variance and unused budgeted capacity | assigned + unused + utilisation var = budget (212,800 + 56,000 + 11,200 = 280,000); spending var = actual - budget | 04 Ch7 | core | MISSING | No budget exists | WP-02 |
| CAP-05 | Split capacity into CAM-I time states (productive, non-productive, idle), cost each state, assign unused cost to the responsible manager, segment or sustaining object, and track idle trend | idle$ = expense × (1 - used/practical); ops owns non-productive, sales owns idle | 05 Ch6, Ch8; 06 MIW ch1, ch5, Frost St6, St8; 04 Ch7 | core | MISSING | none | WP-12 |
| CAP-06 | Model resource supply vs demand: spending profiles (flexible, committed-fixed, committed-step), variability scale 1-5, lumpy steps, several resources per activity (capacity = first constraining resource), bottlenecks | supply step: truck per 800 shipments, demand 1.6 → supply 2.0, unused 0.4; capacity_A = min_r(supply_r / use_r); throughput = min(step capacity) | 04 Ch6-7, Ch14-15; 05 Ch8; 06 Frost St6, St8; 07 Ch9; 08 Ch5 | advanced | MISSING | none (native MRP work-center load to verify for bottlenecks) | WP-12 |
| CAP-07 | Engineering reliability metrics (availability, MTBF/MTTR) and real-time capacity status / APS | A = uptime/(uptime+downtime); R(t) = e^(-λt) | 08 Ch5; 05 Ch6, Ch8 | niche | OOS | Maintenance/MES domain: native Odoo Maintenance (MTBF/MTTR) and MRP planning | - |

### ABC - Activity-based costing
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| ABC-01 | Maintain an activity dictionary: code, verb-noun name, owner, function and process codes, hierarchy (process > activity > task) with roll-up and macro activities | higher level = Σ lower level | 01 BB 4.5; 02 VII.3.1; 04 Ch6, Ch8; 05 Ch1, Ch9; 06 MIW ch4, ch7; 07 Ch5, Ch14; 08 Ch5 | core | PARTIAL | Activity = name, code, default driver (cmc_abc/models/cmc_abc.py:16-36). Missing: owner, process, hierarchy, macro | WP-07 |
| ABC-02 | Build a resource layer from the GL (center × account pools; effort vs non-effort; superfringe) reconciled to 100% of in-scope P&L, with a bill of costs per activity. TDABC resources take their cost from the ledger as well | Σ resources = in-scope P&L expense | 05 Ch1-2, Ch9; 06 MIW ch4, ch7, Frost St6; 07 Ch5, Ch14; 08 Ch5 | core | PARTIAL | Analytic lines are the base, but there is no resource object or pool and no P&L reconciliation; TDABC resource cost is typed (cmc_tdabc.py:92-124) | WP-07 |
| ABC-03 | Assign resources to activities with resource drivers (%-time survey matrix, measured usage, direct charge), each resource 100% assigned | cost_A = Σ_r cost_r × driver%_r,A; e.g. 2/10 × 50% of 100,000 = 10,000 | 01 BB 4.5; 02 VII.3.2; 04 Ch6, Ch12; 05 Ch1-2, Ch9; 06 MIW ch4, ch7; 07 Ch5, Ch14; 08 Ch4-5 | core | MISSING | Stage 1 is a tag: 1 line = 1 activity, no % split (cmc_abc/models/account_analytic_line.py:7-13) | WP-07 |
| ABC-04 | Compute activity rates (on practical capacity) and charge cost objects; produce a bill of activities and unit cost per output | rate = pool / driver qty (22,605,000/2,000,000 = 11.3025); object = Σ qty × rate; unit = total / units | 01 BB 4.5; 02 VII.3.3; 04 Ch6-7, Ch9; 05 Ch1-2; 06 MIW ch4, Frost St5-6; 07 Ch3, Ch5; 08 Ch4 | core | PARTIAL | Pro-rata spread works (cmc_abc.py:75-128) and the reclass report shows activity × product. The rate is not stored, the denominator is actual (not practical) driver volume, and there is no unit cost | WP-07 |
| ABC-05 | Tag activities with a cost-hierarchy level (unit, batch, product, customer, facility sustaining) and enforce the matching driver level; batch drivers | setups = units / batch size (9,000/100 vs 1,000/1,100) | 01 BB 4.5, 4.7; 04 Ch6, Ch9; 05 Ch2-3; 07 Ch3; 08 Ch4-5 | core | MISSING | none (inventory §13 #11c) | WP-07 |
| ABC-06 | Support many cost-object types and hierarchies (product, customer, channel, order, supplier, segment, project, market) with cost-object → cost-object drivers | customer cost = Σ purchased qty × product/order-type unit cost | 01 BB 4.4, 4.7; 04 Ch6; 05 Ch2-3; 06 MIW ch3-4; 07 Ch5, Ch10; 08 Ch4, Ch8 | core | PARTIAL | ABC: products only; TDABC: product and customer (cmc_tdabc.py:239+). No channel, order or supplier objects, no CO→CO | WP-07 |
| ABC-07 | Trace direct costs (DM, DL, object-specific external costs) straight to objects, bypassing activities, following an assignment ladder (trace > driver > allocation) | object = traced + Σ activity charges | 01 BB 4.5, 4.10; 05 Ch2, Ch6; 06 MIW ch1, ch4; 08 Ch4-5 | core | MISSING | ABC result holds activity pools only (cmc_abc.py:217-232) | WP-07 |
| ABC-08 | Keep a driver catalogue: type (transaction / duration / intensity), level, unit, data source, weighted-index drivers, several drivers per activity, pools by shared driver, group + fan-out for large populations | index = Σ weight × count (1/3-5/10); fan-out: % to 10-20 profiles, then by volume within the profile | 04 Ch6, Ch8; 05 Ch1-2, Ch9; 06 MIW ch7, Frost St3; 07 Ch5, Ch14; 08 Ch4, Ch8 | core | PARTIAL | `cmc.cost.driver` = name/code/UoM (cmc_cost_driver.py:4-23) | WP-07 |
| ABC-09 | Run a multistage network (R→A, A→A secondary to primary, A→CO, CO→CO) with conservation and drill-back of every path | each sender distributes 100% of its accumulated cost pro rata; Σ in = Σ out | 05 Ch1-2; 06 MIW ch4; 07 Ch3, Ch14; 08 Gloss | core | MISSING | Only center→center (closing) and activity→product exist; they are not chained | WP-07 |
| ABC-10 | Keep sustaining and unallocated cost in a separate object with optional absorption. Compute full service cost = ABC + idle burden + SG&A burden, split into F/V | absorption = sustaining × traced_i/Σ traced; idle rate = idle$/ABC = 1/(1-idle%) - 1; 90 + 13.5 + 13.5 = 117 | 01 BB 4.5; 04 Ch12; 05 Ch2, Ch6; 06 MIW ch3, ch7, Frost St6-7; 08 Ch4, Ch9 | core | PARTIAL | An unallocated pool is shown (cmc_abc.py:58-73, 111-119). Missing: sustaining object, absorption option, burdens, F/V split | WP-07 |
| ABC-11 | Compare ABC with traditional volume-based cost per product: distortion %, over/under-costed flag | Δ% = (ABC - trad)/trad; trad = OH/DLH × DLH | 01 BB 4.7-4.14; 02 VII.4; 05 Ch1, Ch3; 07 Ch2-3; 08 Ch4 | core | PARTIAL | Full-costing and ABC runs exist separately; no comparison report | WP-07 |

### TDA - Time-driven ABC
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| TDA-01 | Compute the capacity cost rate | rate = resource cost / practical minutes (80,000/158,400 = 0.50/min) | 04 Ch14; 06 MIW ch7; 07 Ch6 | core | COVERED | cmc_tdabc.py:92-124 | - |
| TDA-02 | Model time equations from characteristics | t = β0 + Σ βi·xi (0.5 + 8.5·hazard + 10·oversize) | 04 Ch14; 05 Ch2; 07 Ch5-6 | core | COVERED | cmc_tdabc.py:127-190 (bool/number characteristics on product and partner) | - |
| TDA-03 | Compare required with available time and cost the unused capacity | required = Σ vol × unit time; util = required/practical | 04 Ch14; 07 Ch6 | core | COVERED | cmc_tdabc.py:392-423 (FTE sizing is under BUD-06) | - |
| TDA-04 | Capture unit times from standards or timesheets and compare estimate vs actual | actual min/unit = tagged hours × 60 / volume | 06 Frost St5; 04 Ch14; 05 Ch2 | core | COVERED | scenarios manual/timesheet (cmc_tdabc.py:194, 239-440) | - |

### ABM - Activity-based management
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| ABM-01 | Roll activities into business processes (horizontal view) and show an activity × department matrix that reconciles with the vertical view | process cost = Σ activity cost | 04 Ch6, Ch8; 05 Ch1-2; 06 MIW ch3, ch7; 07 Ch4-5 | core | MISSING | none | WP-08 |
| ABM-02 | Score activities with user-defined attributes (VA/NVA/gray, efficiency 1-5, importance × performance, strategy fit, F/V, inventoriable) and roll cost up by attribute; Pareto and quadrant/bubble charts | cost per attribute value; efficiency bands 1 <5% … 5 50-100% | 04 Ch6, Ch8; 05 Ch1-2, Ch5; 06 MIW ch4, Frost St8; 07 Ch3, Ch7-8, Ch14; 08 Ch4, Ch8 | core | MISSING | none (inventory §13 #15) | WP-08 |
| ABM-03 | Record cost drivers (root cause, chaining) and performance measures per activity (volume, cost/output, cycle time, quality), with driver vs cost trend | cost per output = pool / volume | 04 Ch4, Ch8; 05 Ch1-2; 07 Ch4-5, Ch7-8; 08 Ch4 | core | MISSING | none | WP-08 |
| ABM-04 | Build improvement business cases and track realised benefits (savings, one-time cost, payback, resources freed or redeployed, product actions) | payback = investment / annual saving (583,300 / 808,291 ≈ 0.7 y) | 04 Ch7-9; 05 Ch3, Ch6; 06 Frost St8-9; 07 Ch7, Ch9-10, Ch15 | core | MISSING | none | WP-08 |
| ABM-05 | Benchmark unit activity costs across sites and periods on a common dictionary (niche: customer-preference value multipliers) | value multiplier = revenue equivalent / VA core cost | 04 Ch6, Ch8; 05 Ch4-6; 07 Ch5, Ch7 | advanced | MISSING | none | WP-08 |

### PRF - Profitability analysis
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| PRF-01 | Produce layered profitability along the sustaining hierarchy (product, brand, line, channel, order type, location), reconciled to the P&L, with revenue from sales | R - DM - unit/batch = M1 - product sustaining = M2 - … ; costs only at the highest causal level | 04 Ch9, Ch13; 05 Ch3-4; 06 Frost St7; 07 Ch3, Ch5, Ch10; 08 Ch4; 01 SQ | core | MISSING | No revenue in any engine except typed direct costing | WP-09 |
| PRF-02 | Produce a customer P&L: net price - product cost - cost-to-serve layers - capital charge = economic profit | Kanthal: 10,000 - 5,600 - 1,000 - 2,000 = 1,400 | 04 Ch10; 05 Ch3, Ch5; 06 MIW ch1, Frost St7; 07 Ch5, Ch10; 01 BB 4.14 | core | PARTIAL | TDABC charges activity cost to customers (cost-to-serve only); no revenue or margin (inventory §13 #16) | WP-09 |
| PRF-03 | Plot the whale curve and quadrant matrices (net margin vs cost-to-serve, revenue vs margin, volume vs profit) | rank by profit desc; cumulative profit % vs cumulative % objects | 04 Ch9-10, Ch12; 05 Ch3; 06 Frost St7; 07 Ch8, Ch10 | core | MISSING | none | WP-09 |
| PRF-04 | Analyse the price waterfall (list → pocket price) and cost-to-serve menu pricing (surcharges, minimum order, cost recovery what-if) | pocket = list - invoice disc. - off-invoice - freight - terms … | 04 Ch10; 06 Frost St3; 07 Ch10; 08 Ch4 | advanced | MISSING | none | WP-09 |
| PRF-05 | Compute supplier total cost of ownership and rank suppliers by cost, not price | TCO = price + procurement activity costs + downstream quality costs (IC 4.76 vs 3.17) | 04 Ch11; 05 Ch2, Ch4; 06 MIW ch3, ch7; 07 Ch9-10 | core | MISSING | none | WP-09 |
| PRF-06 | Provide a transaction-level profit cube (order/invoice line × product, customer, channel, geography, salesperson) with drill-down | each base row reconciles to its summaries | 05 Ch3, Ch7; 06 MIW ch9, Frost St7; 07 Ch5-6, Ch10 | core | PARTIAL | Odoo pivot on run results and analytic lines (cmc_reporting_views.xml) with no revenue and no customer/channel dimensions | WP-09 |
| PRF-07 | Pay sales incentives on margin rather than revenue | commission base = contribution | 05 Ch3; 04 Ch9 | niche | OOS | Compensation policy; native/OCA commission modules | - |

### STD - Standard costing and variances
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| STD-01 | Maintain a standard cost card per product (quantity × price for material, labour, overhead; variable and fixed burden rates per center) with roll-up, standard types (basic / ideal / attainable) and versions (budgetary vs updated/kaizen) | burden_var = (own var + var charged from support centers)/activity volume | 04 Ch3 App, Ch5; 05 Ch3; 02 I.8 | core | PARTIAL | Only `product.standard_price` is read (cmc_costing.py:136-140, 345-358); no cards, versions or roll-up (native BoM cost roll-up to verify) | WP-03 |
| STD-02 | Build a flexible budget at actual volume and actual mix | allowed = fixed budget + Σ std var unit × actual qty | 04 Ch3, Ch5, Ch14 | core | MISSING | none (inventory §13 #9b) | WP-02 |
| STD-03 | Compute the spending variance per center and cost element and keep it in that center | authorized = budget fixed + rate × actual volume; var = actual - authorized (31,864 vs 30,237 → 1,627) | 04 Ch3; 05 Ch3, App | core | MISSING | none | WP-02 |
| STD-04 | Compute product variances: material price and quantity, labour rate and efficiency, overhead spending/efficiency/volume, mix and yield, PPV with TCO context; daily/weekly team variances | price var = (Pa - Ps)·Qa; qty var = (Qa - Qs)·Ps; etc. | 04 Ch3-4, Ch11; 05 Ch3, App; 07 Ch2 | core | MISSING | Only `std_price_delta` (cmc_costing.py:421-426); no MO standard vs actual (inventory §5) | WP-03 |
| STD-05 | Report actual GL expense vs ABC cost (volume × ABC unit cost) by department and resource line, with BU roll-up and a three-way actual / ABC / ABB view | var = actual - Σ vol × ABC unit cost | 06 Frost St6-8, Appx F; 05 Ch3 | core | MISSING | none | WP-12 |

### BUD - Budgeting
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| BUD-01 | Build the annual master budget by center × element × F/V and by business line/area, consistent with the company total, with discretionary authorizations and versions | Σ lines = company total; center budget = fixed + var rate × planned driver (#55: 95,300 + 262,200 = 357,500) | 01 SQ st1-5; 04 Ch3 App, Ch15; 02 I.1 | core | MISSING | No budget model (inventory §13 #9a) | WP-02 |
| BUD-02 | Phase the budget monthly with a seasonality profile | month_m = annual × season%_m; fixed = annual/12 | 01 SQ st6; 04 Ch3 App | core | MISSING | none | WP-02 |
| BUD-03 | Build a 3-5 year business plan split into annual budgets, iterating to a target profit | profit = sales - product cost - personnel - structure - owner pay | 01 SQ st1-4; 03 9.2.8 | core | MISSING | none | WP-02 |
| BUD-04 | Report budget vs actual (and vs prior period) by line/area/center, monthly and YTD, absolute and %, with alerts | var = actual - budget; var% = var / budget | 01 SQ st6, PCM Ch3; 03 8.2; 07 Ch5 | core | MISSING | none (inventory §13 #25) | WP-02 |
| BUD-05 | Show each manager only their own targets and actuals | record rule by owner | 01 SQ "Errori" #3; 07 Ch12, Ch15 | advanced | MISSING | Groups are global (cmc_security.xml:15-91) | WP-01 |
| BUD-06 | Run activity-based budgeting backwards from forecast volume and mix: driver quantities → resource demand → supply by spending profile → shortage/excess and FTE → GL/center budget → standard rates; warn on extrapolation | demand = qty × unit time (2,400 × 40 min = 1,600 h); FTE = Σ h × vol / annual h | 04 Ch15; 05 Ch2, Ch8; 06 MIW ch1, Frost St8; 07 Ch6, Ch12, Ch14; 08 Ch9 | core | MISSING | none (inventory §13 #14) | WP-12 |
| BUD-07 | Performance/outcome-based budgeting (public sector) and budget presentation rules | - | 07 Ch6, Ch15; 01 PCM Ch8 | niche | OOS | Policy and formatting, not computation | - |

### FCS - Forecast and simulation
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| FCS-01 | Produce a forecast / rolling forecast (actual YTD + revised remainder) comparable with budget | forecast = actual_1..m + revised_m+1..12 | 01 SQ intro; 06 Frost St8; 08 Ch9 | core | MISSING | none (inventory §13 #26) | WP-02 |
| FCS-02 | Manage scenarios: copy a baseline, change parameters, compare side by side with delta P&L | Δ = scenario - baseline | 01 SQ, PCM Ch9; 02 II.3; 04 Ch15; 05 Ch8; 07 Ch6; 08 Ch7 | core | PARTIAL | CVR `simulazione` (cmc_cvr.py:42-45); draft runs are editable. No copy and no compare (inventory §13 #27) | WP-11 |
| FCS-03 | Run driver-based what-if on the ABC model (volume/mix/process change → resource demand, revised rates, surplus; cost-reduction alternatives) | predicted cost = forecast driver qty × stable rates | 04 Ch15; 05 Ch8; 06 MIW ch3-4; 07 Ch6, Ch8 | core | MISSING | none | WP-11 |
| FCS-04 | Forecast monthly cash flow from the budget and payment terms (and from sales × unit MC) | net cash_m = inflows_m (terms 2/10 net 30, milestones) - outflows_m | 01 PCM Ch11-12; 03 1.2; 08 Ch8 | core | MISSING | none | WP-13 |

### KPI - KPIs and reporting
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| KPI-01 | Keep a KPI library with targets, thresholds, traffic lights and trends (including the €/h center coefficient and cost-per-driver trend) | status = f(value vs target) | 01 SQ "5 segreti" #2; 02 V.1.4; 03 2.1.1, 13.2; 06 Frost St7; 07 Ch5, Ch7 | core | PARTIAL | Cockpit: 4 fixed KPIs + a doughnut (cmc_cockpit.py:107-131); no targets or alerts | WP-14 |
| KPI-02 | Track non-financial process measures (PPM, first-pass yield, scrap, cycle/lead time, OTD, MCE) | MCE = processing / (processing + inspection + move + wait) | 04 Ch4; 06 MIW ch1; 07 Ch4-5; 08 Ch4 | core | MISSING | none | WP-14 |
| KPI-03 | Run a balanced scorecard (perspectives, weights, cascade, links to activities). Niche: pseudo-profit centers | weights per level = 100% | 05 Ch5; 07 Ch14-15; 08 Ch4; 04 Ch5 | advanced | MISSING | none | WP-14 |
| KPI-04 | Publish a monthly management pack within days of close, combining co.ge., co.an., budget and physical data | - | 01 SQ "Pregiudizi", Conclusione; 02 I.1 | core | PARTIAL | One PDF per run (report_reclass.xml, report_consuntivo.xml); no period pack | WP-14 |
| KPI-05 | Statistics on samples (descriptive, correlation, hypothesis tests, regression, PCA, clustering, data mining) | - | 03 10.4-10.8, 2.4.3; 07 Ch10 | advanced | OOS | Generic statistics not tied to a cost computation | - |

### FIN - Financial statement analysis (analisi di bilancio)
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| FIN-01 | Reclassify the balance sheet by financial criterion (current/non-current) and functional criterion, with the summary aggregates | Imm. netto = AopNC - PopNC; CCNop = AopC - PopC; COIN = Imm. netto + CCNop; CIN = COIN + att. fin.; PFN = pass. fin. - att. fin.; COIN = PN + PFN | 03 3.2-3.4 | core | MISSING | inventory §13 #23 | WP-13 |
| FIN-02 | Configure mapping rules account/voce → aggregate with overrides (maturity >12m, obsolete stock, short-term portion of LT debt, shareholder loans as equity), a reduced mode for abbreviated statements, and a register of choices | rule-based mapping + manual override | 03 3.2.1-3.4.2, 4.5, 2.1.1, 1.4, 6.3.1 | core | MISSING | none | WP-13 |
| FIN-03 | Reclassify the P&L at value added (+ distribution of value added) | VP - external costs = VA - personnel = MOL - non-monetary = MON ± fin. income = EBIT - OF = ordinary ± extraordinary = pre-tax - taxes = net | 03 4.4-4.5 | core | MISSING | none | WP-13 |
| FIN-04 | Reclassify the P&L at cost of sales and at contribution margin (company level) | R - COGS = industrial margin - overheads = EBIT; R - CV = MC - CF = EBIT | 03 4.2-4.3; 02 VI.4 | core | MISSING | Direct costing is manual, product level only | WP-13 |
| FIN-05 | Analyse a series of ≥3 years: common-size, YoY trend, distortion flags, sector benchmark (ATECO, 19 indices) | var% = (X_t - X_t-1)/X_t-1; row / revenue | 03 8.2, 2.4, 14, 2.5 | core | MISSING | none | WP-13 |
| FIN-06 | Compute liquidity: treasury margin, quick ratio, CCN, current ratio, cash coverage; DSO/DPO/DIO and commercial cycle with what-if | DSO = 360/(sales/receivables); cycle = DSO + DIO_rm + DIO_fg - DPO (43+31+18-32 = 59) | 03 5.2-5.6 | core | MISSING | none | WP-13 |
| FIN-07 | Compute solidity: composition, autonomy, D/E, structure margin and coverage, time matching of sources and uses, financing intensity, interest coverage | autonomy = PN/total; D/E = PFN/PN; structure margin = PN - ANC; coverage OF = MOL/OF | 03 6.2-6.5, 13.4.3 | core | MISSING | none | WP-13 |
| FIN-08 | Compute profitability and leverage: ROE variants, ROA, ROI, ROIop, DuPont (ROS × turnover), ROD, Modigliani-Miller leverage, productivity per employee | ROIop = MON/COIN = ROS × R/COIN; ROE = [ROI + (ROI - ROD)·D/PN]·S·I | 03 8.3-8.11, 1.2 | core | MISSING | none | WP-13 |
| FIN-09 | Produce cash-flow statements: gross cash flow; functional statement by management areas (FCOC, FCO, FCFE, Δcash reconciled); OIC 10 indirect/direct; liquidity dynamics | gross CF = net income + non-monetary costs; FCOC = autofin. - ΔCCNop ± Δfunds | 03 5.6.1, 7.4-7.9 | core | MISSING | none (native account_reports cash-flow layout to verify) | WP-13 |
| FIN-10 | Compute crisis indicators by area (equity, economic, financial) with thresholds, and data-quality red flags | e.g. PN + Pnc - Anc > 0; FCO > debt service; ROI vs ROD | 03 13.2-13.6 | core | MISSING | none | WP-13 |
| FIN-11 | Compute WACC (CAPM), EVA, MVA/EV, and the capital charge per product/customer (economic profit) | WACC = Ke·E/(D+E) + Kd(1-t)·D/(D+E); Ke = rf + β(rm - rf); EVA = NOPAT - WACC × COIN | 03 9.2; 04 Ch13; 05 Ch5; 08 Ch5, Ch8 | advanced | MISSING | none | WP-13 |
| FIN-12 | Reclassify by SBU/segment: MSLC, allocated PFN, cash flow per SBU, sustainable growth | FCN = MSLC - ΔCOIN = COIN_0 × (ROCE - g) | 03 12.3-12.6 | advanced | MISSING | none | WP-13 |
| FIN-13 | Reconcile management accounting with the GL/P&L (closed system; neutral and figurative items; resources = activities = objects) | Σ COAN + excluded - figurative = Σ COGE in scope | 02 I.1-I.2, V.1.5; 04 Ch2, Ch14; 05 Ch1-2; 06 MIW ch1, ch8; 07 Ch3, Ch14; 08 Ch5 | core | PARTIAL | Closing conservation guard (cmc_closing.py:165-215); ABC unallocated rows. Missing: a COGE↔COAN reconciliation report | WP-01 |
| FIN-14 | Statutory and group reporting: IFRS schemes (IAS 1/7), consolidation, group leverage, IAS 14 segments | - | 03 3.5, 4.6, 7.10, 11, 12.3 | advanced | OOS | Statutory/group consolidation belongs to native account_reports and multi-company consolidation | - |
| FIN-15 | Valuation by multiples, analysis profiles by purpose, legal restructuring support, project financing/loans | - | 03 9.3, 2.2, 13.7-13.10; 01 PCM Ch11 | niche | OOS | Corporate finance and legal advisory; loans are native accounting | - |
| FIN-16 | Depreciation methods (straight-line, SYD, accelerated, activity-based) | (cost - salvage)/years; SYD n/Σyears | 01 PCM Ch7; 08 Ch5; 05 Ch6; 07 Ch14 | core | OOS | Native account_asset. Replacement-cost depreciation for costing can be entered as a figurative cost (cmc_figurative) | - |

### PRJ - Project cost management
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| PRJ-01 | Estimate project cost: top-down, bottom-up by WBS, analogous, parametric, rate cards, accuracy bands, PERT/three-point | E = (O+4M+P)/6; σ = (P-O)/6; range = √Σσ²; triangular (O+M+P)/3 | 01 PCM Ch3, Ch6, App C; 08 Ch2 | core | MISSING | none | WP-15 |
| PRJ-02 | Set a time-phased cost baseline by WBS with roll-up, contingency reserve from a risk register and a management reserve (risk ranking) | CR = Σ EMV = Σ P × I; budget = baseline + MR; RF = p × i | 01 PCM Ch1, Ch7-8; 08 Ch3, Ch5-6 | core | MISSING | none | WP-15 |
| PRJ-03 | Compare planned vs actual per period with trend, control charts (limits, rule of seven), change requests and re-baselining | out of control if a point is outside limits or 7+ points sit on one side | 01 PCM Ch1, Ch3, Ch9; 05 Ch6 | core | MISSING | none | WP-15 |
| PRJ-04 | Run earned value management (status report, S-curve) | CV = EV - AC; SV = EV - PV; CPI = EV/AC; SPI = EV/PV; EAC = BAC/CPI; ETC = EAC - AC; VAC = BAC - EAC; TCPI = (BAC - EV)/(BAC - AC) | 01 PCM Ch10 | core | MISSING | inventory §13 #21 | WP-15 |
| PRJ-05 | Commitment tracking, PM methodology (plan template, BoE, closeout, audit, agile sizing), contract/payment types, VAT/escalation | - | 01 PCM Ch1-14 | advanced | OOS | Native budget.analytic committed amounts, project and sale billing; methodology is not software | - |

### TGT / LCC / COQ - Target costing, life cycle, quality cost
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| TGT-01 | Run target costing (allowable cost, gap, deployment to components/functions, supplier target price) and kaizen costing | allowable = target price - target margin [- non-mfg]; Σ component targets = product target; kaizen = (1 - r%) × (fixed + flexible var at actual mix) (0.94 × 230,890 = 217,037) | 04 Ch5, Ch11 App; 05 Ch3; 06 MIW ch1, ch3; 07 Ch8; 08 Ch2 | core | MISSING | inventory §13 #18 | WP-16 |
| TGT-02 | Cost design features (design variables → driver qty × rates) and keep design cost guidelines | cost = Σ feature driver × rate (SMT 1.0x, robot 3x, hand 6-10x) | 04 Ch11; 05 Ch3; 07 Ch8-9; 08 Ch5-8 | advanced | MISSING | none | WP-16 |
| LCC-01 | Run life-cycle costing and lifetime profit per product/customer (scope and horizon, LC stages, non-recurring costs kept separate, committed vs incurred). Niche extensions: maintenance events, end-of-life, environmental, liability EMV | LC unit cost = LC cost / lifetime volume (20,000/10,000 = 2), recomputed on residuals | 04 Ch13; 05 Ch3; 06 Frost bg, St4; 07 Ch8-9, Ch14; 08 Ch1-2, Ch5, Ch7-8 | advanced | MISSING | inventory §13 #19 | WP-16 |
| LCC-02 | Derive a costing view and a cash-flow/NPV view from the same driver base (incremental vs moving baseline, alternatives) | cost ≠ expense: unused = expense - cost; NPV per alternative | 08 Ch2, Ch5-6, Ch8 | advanced | MISSING | none | WP-16 |
| COQ-01 | Classify activity costs as prevention / appraisal / internal failure / external failure (+ error-free, reconciled), track the trend, and compute cost of poor quality per product | CoPQ = defect units × ABC cost accumulated to detection point | 04 Ch8; 05 Ch2, Ch6; 06 MIW ch4; 07 Ch9, Ch14; 08 Ch1, Ch4 | advanced | MISSING | inventory §13 #20 | WP-08 |

### RSK - Uncertainty
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| RSK-01 | Run Monte Carlo / Latin Hypercube on uncertain inputs (normal, triangular, uniform): forecast-cell statistics, percentiles, P(profit>0), confidence bands, risk-based budgeting | error ≈ 1/√N; N ≥ 6 × variables | 08 Ch3, Ch7-9, App A | advanced | MISSING | none | WP-11 |
| RSK-02 | Run sensitivity analysis (rank correlation, tornado/spider at ±10%, sensitivity of driver estimates) with significance rules | trust abs(ρ) ≥ 0.05, dominant > 0.10 | 08 Ch6-8, App A; 05 Ch2, Ch9 | advanced | MISSING | none | WP-11 |

### INT - Integration, data and governance
| ID | Capability | Formula / algorithm | Sources | Weight | Status | Evidence / what is missing | WP |
|---|---|---|---|---|---|---|---|
| INT-01 | Use actual costs from the GL as the cost base, automatically | analytic lines from posted moves | 04 Ch14; 05 Ch1; 06 MIW ch1; 07 Ch13-14 | core | COVERED | native analytic + cmc_account bridge (inventory §2) | - |
| INT-02 | Load driver quantities from feeder systems (MRP, sales, purchasing, timesheets) through a transaction → driver mapping, refreshed on a schedule | driver qty = count/sum of mapped transactions per object | 04 Ch14; 05 Ch1-2, Ch11; 06 MIW ch9, Frost St3, Appx A; 07 Ch13-15 | core | PARTIAL | TDABC `volume_derived` + timesheet hours (cmc_tdabc.py:239-440). Costing and ABC driver quantities are typed (cmc_abc.py:184-213) | WP-07 |
| INT-03 | Bridge material and labour from MRP and timesheets without double booking | MP at SVL value; MOD from timesheet/MO labour; twin excluded | 01 BB Ch1; 02 V.2; 05 Ch2, Ch6 | core | COVERED | cmc_mrp/models/mrp_production.py:67-201; cmc_hr_timesheet account_analytic_line.py:24-50 | - |
| INT-04 | Version models and runs (period/scenario copies, assumptions register, child→parent consolidation) | - | 05 Ch2-3, Ch9; 06 MIW ch6, ch8-9; 07 Ch12, Ch15; 08 Ch5, Ch7 | advanced | PARTIAL | draft→done lock and reset (cmc_run_mixin.py:12-104). No copy, version or assumption register | WP-11 |
| INT-05 | Validate data: control totals at each stage, 100% assignment, fail-loud, conserving rounding, driver volumes vs system totals, reasonableness vs prior period | Σ in = Σ out per stage | 07 Ch10, Ch14; 08 Ch5, Ch7; 02 IV.2 n47 | core | PARTIAL | Guards and fail-loud (cmc_closing.py:165-215, 254; cmc_utils.py:4-36). Missing: driver vs system check, prior-period check | WP-01 |
| INT-06 | Run multi-company | company rules | 03 11 | advanced | COVERED | cmc_security.xml:30-91 | - |
| INT-07 | Run multi-currency (budget currency, conversion) | fixed internal budget rates | 01 PCM Ch2, Ch7 | advanced | PARTIAL | Company currency only, no conversion layer (inventory §13 #28b) | WP-02 |
| INT-08 | Methodology: interviews, questionnaires, implementation project, pilots and rapid prototyping, ABC diagnostics (Sutton/diversity), software selection, storyboarding, variance-cause checklist, isolating vs non-isolating allocation policy, warnings on surveys, discrete-event simulation/TOC | - | 01 BB 4.2; 02 I.6 box; 04 Ch6, Ch8; 05 Ch8-9; 06 MIW ch6, Frost St5, Appx E-F; 07 Ch8, Ch12-13; 08 Ch4 | niche | OOS | Organisational advice or change management, not a software function | - |
| INT-09 | Open-book inter-firm cost sharing; notes on service-sector applicability | - | 04 Ch10-11; 05 Ch4; 02 VI.4 | niche | OOS | Advisory | - |

---

## 2. Work packages (MISSING + PARTIAL, core and advanced)

All 102 gap items (MISSING + PARTIAL; 83 core, 19 advanced) are assigned, each to exactly one WP. There are no niche gaps outside OOS: niche variants were folded into their parent capability. Efforts assume AI-assisted development and include tests (simple model + view + tests ≈ 3-6 h; engine with tests ≈ 6-12 h).

| WP | Name (proposal) | Depends | CAP ids | Main models (high level) | Engines | Reports | Hours | Native/OCA reuse (to verify by scout) |
|---|---|---|---|---|---|---|---|---|
| WP-01 | `cmc_base` v2: foundation hardening (in-place extension of cmc_base + cmc_account) | - | CEN-02, CEN-03, CLS-02, CLS-04, ALC-01, ALC-04, ALC-08, CAP-01, BUD-05, FIN-13, INT-05 (11) | `cmc.responsibility.center` (type, owner, M2M centers), controllable/avoidable/sunk flags on element/line, `cmc.cost.function` (K, v, range, method), driver criterion/rationale fields, step-down sequence, normal/budget capacity | hierarchy roll-up, high-low/OLS estimator, allocation → cost-base posting, range check, reconciliation COGE↔COAN, data validation | hierarchical cost pivot, controllable cost per manager, reconciliation report, allocation-share report | 57 | Odoo 19 analytic plans have their own hierarchy: verify whether roll-up can use the plan tree instead of `cmc_parent_id` |
| WP-02 | `cmc_budget`: master/flexible budget, variances, forecast | WP-01 | BUD-01, BUD-02, BUD-03, BUD-04, FCS-01, STD-02, STD-03, CAP-04, ALC-06, INT-07 (10) | `cmc.budget` (version, scenario, years), `.line` (center × element × F/V × line, var rate), seasonality profile, forecast version | phasing, flexible budget at actual mix, spending + capacity-utilisation variance, GPK dual-rate closing, forecast = actual YTD + revised | BvA monthly/YTD + prior year, variance by center/element, forecast vs budget | 65 | Native `account_budget` (budget.analytic, Enterprise) and OCA `mis_builder_budget` / `account_budget_oca`: reuse for header/lines/BvA; keep flexible budget, variances and GPK in CMC |
| WP-03 | `cmc_standard_cost` | WP-02, cmc_mrp, cmc_full_costing | STD-01, STD-04, FC-08, FC-09 (4) | `cmc.standard.card` (+ lines MP/MOD/OH, version, type), predetermined rate table | card roll-up from BoM/routing, MO variance engine (price/qty/rate/eff/OH spending-eff-volume, mix/yield), over/under absorbed, standard_price push + reconciliation | variance analysis per MO/product/period, absorption report | 50 | `mrp_account` "Compute price from BoM", `mrp_account_enterprise` MO cost analysis, stock valuation in standard mode: verify before building the card and the posting |
| WP-04 | `cmc_full_costing` v2 | WP-01, cmc_mrp | FC-04, FC-05, FC-10, FC-11, CAP-03 (5) | produced qty on costing result, cost-plus price list fields | actual prime cost from MP/MOD lines, unit cost, Cu curve, variable vs full view, used-capacity charging (practical denominator) | unit cost card, variable vs full comparison, cost-plus price proposal | 28 | MRP produced quantities (stock.move) as source; no OCA equivalent |
| WP-05 | `cmc_job_costing` (commesse) | WP-04, cmc_hr_timesheet, project (FC-08 rates from WP-03 optional) | FC-06, CEN-04 (2) | job cost sheet on project/analytic account, resource-center rates | hours × resource rate, overhead applied, WIP/close-to-FG | job cost sheet PDF, job margin | 24 | Native project profitability (`project_account`, `sale_timesheet`), hr employee hourly cost: check overlap first |
| WP-06 | `cmc_process_costing` | WP-04, cmc_mrp | FC-07 (1) | `cmc.process.run` per department/period, WIP % lines | EU (WA + FIFO), spoilage normal/abnormal, transferred-in chaining, control total | cost-of-production report | 22 | none known |
| WP-07 | `cmc_abc` v2: multistage ABC engine | WP-01 | ABC-01…ABC-11, INT-02 (12) | activity tree (process/level/owner), `cmc.abc.resource` (pool from GL), resource-driver matrix, driver catalogue (type/level/source/weights), cost-object types incl. channel/order/supplier, network edges | R→A→A→CO→CO solver with conservation and drill-back, practical-capacity rates, direct trace, sustaining/idle/SG&A burdens, F/V split, feeder-driver import (MRP/sale/purchase counts) | bill of activities, bill of costs, ABC vs traditional, network trace | 84 | No OCA ABC module known: verify |
| WP-08 | `cmc_abm` (incl. cost of quality) | WP-07 | ABM-01…ABM-05, COQ-01 (6) | attribute definitions/values on activities, process, performance measures, improvement initiative, benchmark set | attribute roll-up, process view, CoPQ | VA/NVA Pareto, quadrant/bubble, process cost, initiative tracking, COQ trend | 42 | `quality_control` (Enterprise) as source of defects/scrap |
| WP-09 | `cmc_profitability` | WP-07, cmc_account, sale, account | DC-02, DIF-05, ALC-07, PRF-01…PRF-06 (9) | profit fact table (invoice line + assigned costs), sustaining hierarchy levels, price-waterfall elements, supplier TCO, transfer-price rules | layered margin engine, customer P&L / cost-to-serve, TCO, transfer pricing / service rates, price-floor check | product/customer/channel P&L, whale curve, quadrants, waterfall, supplier ranking | 62 | `sale_margin` (native) for gross margin; spreadsheet dashboards for curves |
| WP-10 | `cmc_direct_costing` v2: decisions | WP-07 (for DIF-03/DIF-04), cmc_direct_costing | DC-03, DC-05, DC-08, DC-09, DIF-01, DIF-03, DIF-04, DIF-06 (8) | persisted `cmc.decision` with line items, quote costing, investment cash-flow table | constrained mix/TOC, tax/margin target, lever sensitivity + iso-profit, multi-product volume mix, indifference Q*, NPV/IRR/payback with inflation | decision report, quote cost build-up, sensitivity table | 49 | none (`account_asset` only for depreciation) |
| WP-11 | `cmc_scenario` | WP-02, WP-07, WP-10 | FCS-02, FCS-03, RSK-01, RSK-02, INT-04 (5) | scenario/version copy of runs, distributions on inputs, assumption register | scenario cloning + compare, ABC driver what-if, Monte Carlo/LHS, rank-correlation and tornado | scenario delta P&L, histograms/percentiles, tornado | 49 | Spreadsheet (Enterprise) as a lightweight what-if alternative |
| WP-12 | `cmc_abb_capacity` | WP-02, WP-07 | BUD-06, CAP-05, CAP-06, STD-05 (4) | resource spending profile (flexible/committed/step, unit size), time-status modes, capacity responsibility assignment | reverse ABC (demand → supply, FTE, shortage/excess), first-constraining-resource capacity, ABC variance and three-way | ABB budget by GL/center, capacity modes, actual vs ABC vs ABB | 40 | MRP work-center capacity/load for bottlenecks |
| WP-13 | `cmc_fin_analysis` (analisi di bilancio + cash forecast) | account; WP-02 (FCS-04); WP-09 (FIN-12 SBU) | FIN-01…FIN-12, FCS-04 (13) | reclassification scheme + mapping/override rules + choices register, ratio definitions, sector benchmark import | BS/P&L reclassification, ratio pack, functional and OIC 10 cash flow, crisis indicators, EVA/WACC, SBU allocation, cash forecast from budget + terms | reclassified BS/P&L multi-year, ratio dashboard, cash-flow statements, crisis panel | 87 (≈45 if built as mis_builder templates) | **Strong reuse candidate**: OCA `mis_builder` (+ `mis_builder_cash_flow`), native `account_reports` custom report engine, l10n_it statutory reports. Scout first |
| WP-14 | `cmc_kpi` | WP-02, WP-13 | KPI-01…KPI-04 (4) | KPI definition (source, formula, target, thresholds, perspective, weight), measurement history | KPI compute + status, non-financial ingestion (MRP/quality/delivery), BSC weighting | KPI dashboard, BSC, monthly management pack | 32 | Spreadsheet dashboards (Enterprise), OCA `kpi`/`kpi_dashboard`, mis_builder KPI |
| WP-15 | `cmc_project_control` (EVM) | WP-05, project | PRJ-01…PRJ-04 (4) | project estimate (WBS on tasks, rate card, PERT), time-phased baseline, risk register, change request | PERT, EMV reserves, EVM indices and forecasts, control chart | EVM status report, S-curve | 32 | `project_account_budget` / project forecast (Enterprise), OCA project budget modules |
| WP-16 | `cmc_target_lcc` | WP-07, WP-10 | TGT-01, TGT-02, LCC-01, LCC-02 (4) | target cost (product/component), kaizen target, LC model (phases, horizon, non-recurring) | allowable cost + gap deployment, feature costing from rates, LC unit cost, NPV cash view (reuses WP-10) | target vs current gap, LC profit per product | 30 | none |
| | **Total** | | **102 CAP** | | | | **753** (≈711 with WP-13 on mis_builder) | |

Check: 11+10+4+5+2+1+12+6+9+8+5+4+13+4+4+4 = 102 = every MISSING + PARTIAL row of §1 (verified by script; no gap is left without a WP).

### Dependency order
1. **WP-01** foundation (no dependencies)
2. **WP-02** budget → unlocks WP-03, WP-12, WP-13 (cash forecast), WP-14
3. In parallel after WP-01: **WP-04** → WP-05 → WP-15; WP-04 → WP-06
4. **WP-07** ABC v2 (after WP-01) → WP-08, WP-09, WP-10 → WP-11, WP-16; WP-07 + WP-02 → WP-12
5. **WP-13** can start right after WP-01 (or on its own if built on mis_builder); its FCS-04 part needs WP-02 and its FIN-12 part needs WP-09
6. **WP-14** last (needs WP-02 and WP-13)

Suggested release waves: **A** = WP-01, WP-02, WP-04, WP-13 (core Italian PMI controllo: budget, BvA, actual product cost, analisi di bilancio; ≈ 237 h). **B** = WP-03, WP-05, WP-07, WP-09, WP-10 (≈ 269 h). **C** = WP-06, WP-08, WP-11, WP-12, WP-14, WP-15, WP-16 (≈ 247 h).

---

## 3. Top MISSING core capabilities (priority for Italian PMI controllo di gestione)
1. BUD-01 Annual master budget (center × element × F/V, lines/areas) - WP-02
2. BUD-04 Budget vs actual monthly/YTD with alerts and prior-year comparison - WP-02
3. STD-02 / STD-03 Flexible budget and center spending variance - WP-02
4. FCS-01 Forecast / rolling forecast - WP-02
5. FIN-01 / FIN-03 Reclassified balance sheet (financial + functional) and value-added P&L - WP-13
6. FIN-06…FIN-08 Ratio pack: liquidity, solidity, profitability, DuPont, leverage - WP-13
7. FIN-09 Cash-flow statement (functional and OIC 10) + FCS-04 cash forecast - WP-13
8. STD-04 Product variances (price/qty/rate/efficiency/volume) - WP-03
9. FC-06 Job-order costing (commessa) - WP-05
10. PRF-01 / PRF-03 Layered product/customer profitability with whale curve - WP-09
(Next: ABC-03 resource drivers, BUD-06 ABB, PRJ-04 EVM, TGT-01 target costing, FC-07 process costing, FIN-10 crisis indicators.)

---

## 4. Coverage statistics

Out of scope (10 rows: 1 core, 3 advanced, 6 niche) are excluded from the percentages. In-scope base = **126** capabilities.

### Before the WPs (current suite)
| Weight | In scope | COVERED | PARTIAL | MISSING | Coverage (COVERED %) |
|---|---|---|---|---|---|
| core | 105 | 22 | 34 | 49 | 21% |
| advanced | 21 | 2 | 4 | 15 | 10% |
| niche | 0 (all niche rows OOS or folded into a parent) | - | - | - | - |
| **total** | **126** | **24** | **38** | **64** | **19%** (49% counting PARTIAL as half-done) |

By area (COVERED / PARTIAL / MISSING): CEN 1/2/1 · CLS 3/2/0 · ALC 3/3/2 · FC 3/4/4 · DC 4/4/1 · DIF 1/3/2 · JNT 1/0/0 · CAP 1/2/3 · ABC 0/7/4 · TDA 4/0/0 · ABM 0/0/5 · PRF 0/2/4 · STD 0/1/4 · BUD 0/0/6 · FCS 0/1/3 · KPI 0/2/2 · FIN 0/1/12 · PRJ 0/0/4 · TGT 0/0/2 · LCC 0/0/2 · COQ 0/0/1 · RSK 0/0/2 · INT 3/4/0.
The suite is strong on the closing/ribaltamento pipeline, full costing, CVP, TDABC and joint costs. It has nothing for budget/variance, analisi di bilancio, ABM, profitability with revenue, project control and target/LCC.

### After the WPs (planned)
| Scenario | COVERED core | COVERED advanced | COVERED total | % of in-scope |
|---|---|---|---|---|
| Today | 22 | 2 | 24 | 19% |
| After wave A (WP-01, 02, 04, 13; ≈237 h) | 55 | 8 | 63 | 50% |
| After wave B (+WP-03, 05, 07, 09, 10; ≈269 h) | 87 | 11 | 98 | 78% |
| After wave C (+WP-06, 08, 11, 12, 14, 15, 16; ≈247 h) | 105 | 21 | 126 | 100% |

Wave counts are exact (script count of the WP column). All 102 gaps are mapped, so delivering every WP gives 126/126 in scope (100%; 10 OOS rows stay with native Odoo or as methodology). The ≈753 h total is AI-assisted and includes tests. It drops to ≈700 h if the scout confirms that mis_builder / account_reports (WP-13), account_budget (WP-02) and the native MRP cost analysis (WP-03) can be reused.
