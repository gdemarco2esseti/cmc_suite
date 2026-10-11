# Release notes — fix stress test CMC (2026-10-11)

Branch `fix/stress-2026-10`, created from `fix/review-2026-09-17` @ f21feba.

This release fixes the defects found by the UI stress tests of 2026-10-10:
- **r1:** full suite, 40 scenarios;
- **r2:** anomalies from the video guides.

The defect sheets with their regression acceptance criteria are in `stress-test/FX-*.md` of the main checkout. Decisions DL-001..010 were approved on 2026-10-11.

## Behaviour changes

Each change below is user-visible and lists the defect or decision it comes from.

### Cost entries (FX-002, FX-003, DL-001, RN-001)

- In the manual Cost Entries screen, **Cost Center and Cost Behavior are mandatory**. Lines coming from the bridges (invoices, timesheets, MRP) are not affected.
- The Cost Entries list now shows the whole cost base: classified lines plus every line with a negative amount.
  - A new filter, "To Classify", isolates lines without a behaviour. Before this release those lines disappeared from the list.
  - Revenue lines without a behaviour are not part of the cost base. The same domain is used by the cockpit KPI "Booked Costs" and by the cost-by-element chart, through `_cmc_cost_base_domain`.
  - **RN-004:** with `cmc_account` installed, the cost base is decided by the type of the financial account, not by the sign. Credit notes and refunds on cost accounts stay in the cost base, and revenue accounts stay out. The Cost Entries action domain is overridden in `cmc_account`.
  - Running `-u cmc_base` alone reloads the base domain on the action. Always upgrade every cmc module together, so that `cmc_account` re-applies its override.

### Access rights (FX-001, FX-008, FX-019)

- **FX-001:** Controlling Managers can open Configuration → Attributes. They get read access to `ir.model`, the same precedent as `mass_mailing`, and the "Applies To" picker is restricted to models that support CMC attributes.
- **FX-008:** Controlling Responsible users no longer get an access error on the cockpit. The cockpit skips the steps the user cannot read. Archive is no longer offered on responsibility centers.
- **FX-019:** Excluded Journals on financial analyses are visible only with "Show Accounting Features – Readonly". Before this release, the analysis form raised an access error for controllers without accounting rights.
- **RN-005:** "Duplicate" on a financial analysis works for a controller without accounting rights and keeps the excluded journals.
- The cockpit hides the cost-by-element card for users who cannot read cost elements, instead of claiming there is no classified cost.

### Prefill buttons (FX-004, DL-002)

This covers all five prefill buttons: full costing, ABC, ABB, process costing and standard rates.
- They ask for confirmation before replacing lines.
- When there is no source data, they stop with a message saying where to enter it. The existing lines are kept.

### Reset to draft (FX-007, DL-003)

- Every "Reset to Draft" that deletes results now asks for confirmation.
- The label is "Riporta in bozza" across the whole suite.

### Budget vs actual (FX-009, DL-004, DL-006, RN-003)

- Only **approved** versions are shown. Before this release, actuals were counted once per version.
- Plan and actual are on the same row.
- Costs are shown as positive amounts. A favourable variance is green.
- The report opens filtered on the version covering the current month, and grouped by version. If no version covers it, it falls back to the version with the latest end date.
- **RN-006:** when a budget and a forecast both cover the current month, the default is the budget.
- Each approved version is compared with the same actual figures. Without a version filter, the actual is repeated once per version, so totals must be read within one version. This was a user decision on 2026-10-11, and the help of the report says so.
- `_cmc_bva_summary`, used by the KPIs, keeps the natural sign.

### Budget seasonality and month (FX-016, DL-008)

- New "Add Annual Budget" wizard on plan versions, with a seasonality profile.
- New default seasonality field on the version.
- The seasonality field only shows when it applies (variable costs and revenues). A fixed cost is always spread evenly (BUD-02).
- The date of a budget line is labelled "Month".

### Standard cost: push prices (FX-010, DL-005, RN-002)

- "Push Standard Prices" now opens a preview listing each product with its current and new cost.
- The push records a snapshot. Nobody can edit that snapshot, not even a manager.
- After a push, the version can no longer be reset: create a new version instead.
- **FX-020:** "Roll up cards" stops with a message naming the products without a manufacturing bill of materials. Before this release, nothing was created and nothing was said.
- "Prefill Rates" is available to Controlling Users. Before this release it gave an access error.

### Job costing and EVM (FX-012, FX-013)

- **S-curve**
  - Forecast months leave EV/AC empty instead of dropping to 0.
  - The menu opens a line chart showing PV, EV and AC together, backed by the new `cmc.evm.curve.series` model.
  - The chart is filtered on the latest status report, so several runs are no longer summed. It is not stacked.
  - In forecast months EV and AC repeat the status-date figure, so the lines stay flat instead of dropping to 0. This was a user decision on 2026-10-11.
- **FX-013:** Job runs list every job with hours but no cost center in a single message, saying where to set it.

### Profitability and ABB (FX-014, FX-015, FX-006)

- **FX-014:** a profitability run is refused when its ABC run assigns no product, customer or channel cost. Missing ABC dates and dates outside the span now give two distinct messages.
- **FX-015:** ABB variance actual cost includes analytic lines tagged with an activity. The ABC pool itself is unchanged.

### KPIs (DL-010)

- A KPI with no data gets the new "No data" status, instead of being shown "on target" or green.
- Formula KPIs are excluded from the 0/0 rule, unless every input of the formula has no data: then the formula KPI has no data too.
- The unit of ratio KPIs follows the financial ratio, so ROS is shown as a percentage.
- In KPI Measurements, values and targets are shown with their unit. A percent ratio stored as a fraction (0.9076) shows as 90.76 %, the same figure as the ratio list.
- Sector benchmarks of percent ratios are typed as percentages (7.5 = 7.5 %) and stored as fractions. Existing benchmarks are **not** converted: check the ones entered as percentages.
- **RN-007:** the targets of percent ratio KPIs, both the static target and the plan-version targets, are typed and read as percentages (15 = 15 %) and stored as fractions. Percent KPIs that already hold 0..100, such as MRP formulas, are unchanged.
- **RN-008:** the management pack PDF prints KPI values, targets and ratios with their unit, as the screens do. Pivot and graph views still aggregate the raw stored value, so a percent ratio appears there as a fraction.
- One shared helper in `cmc_base`, `cmc_utils.percent_factor` / `format_figure`, does the fraction ↔ percent conversion for KPIs, ratios and benchmarks.

### Display (FX-005, FX-011, FX-017, FX-018)

- **FX-005:** the CVR "has break-even" column shows a checkbox instead of raw HTML.
- **FX-011:** the CMC cost center on manufacturing orders now has a label.
- **FX-017:** state and center-type labels are translated into Italian in every module.
- **FX-018**
  - Years are shown without a thousands separator.
  - Ratio values and thresholds are no longer summed in group totals.
  - The ROI dialog keeps its title after Compute.
  - Ratio value lists show value, threshold and benchmark in a single column with their unit ("Ratio Value", "Ratio Threshold", "Ratio Benchmark").
- **FX-006:** the joint cost message names the line columns to fill.

## Upgrade

Run `-u` on **all** cmc modules, as with the previous release, **with `--i18n-overwrite`**. Without it, Odoo keeps Italian terms already in the database: for example "Reimposta a bozza" instead of "Riporta in bozza".

New migrations:

| Module | Version | Effect |
|---|---|---|
| `cmc_job_costing` | 19.0.1.2.1 (post) | EV/AC are set to empty on the forecast rows of curves already computed. |
| `cmc_kpi` | 19.0.1.1.0 (post) | The unit of existing ratio KPIs is recomputed. |

Tested on copies:
- `cmc_all` (from 19.0.1.0.0): `-u` plus `-i cmc_suite`, exit 0.
- `cmc_video`: both migrations ran. They nulled 9 forecast rows and recomputed the KPI units.

## Known limits

- **DL-007:** no upper bound on cost entry amounts, and negative costs are still accepted.
- **DL-009:** the cost pivot sums every company active in the selector, which is standard Odoo behaviour.
- **VG-11:** the XML-RPC TypeError could not be reproduced. A traceback is needed.
