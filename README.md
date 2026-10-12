# cmc_suite

Odoo 19 management control (controllo di gestione) suite: cost taxonomy and centers, closing
(ribaltamento), full costing, direct costing / CVR, ABC, joint costs, TDABC, plus bridges to
accounting, timesheets and manufacturing.

| Module | Role |
|---|---|
| `cmc_base` | cost taxonomy, cost centers, Cost Entries, closing runs, capacity, cockpit, run lifecycle (`cmc.run.mixin`) |
| `cmc_full_costing` | full cost per product (single/multiple base), figurative costs |
| `cmc_direct_costing` | contribution margin, CVR / break-even, decisions |
| `cmc_account` | classification rules, manual overrides, Unclassified queue, bridge profiles hook |
| `cmc_hr_timesheet` | timesheet hours as direct labour (MOD) |
| `cmc_mrp` | manufacturing materials (MP) and labour; labour twin exclusion |
| `cmc_abc` / `cmc_tdabc` | activity-based and time-driven activity-based costing |
| `cmc_joint_costs` | joint and by-product cost allocation |
| `cmc_timesheet_grid` | Enterprise bridge: classify validated timesheets |
| `cmc_suite` | installs the whole suite |

## Conventions

- **Sign:** a cost is a negative analytic amount (Odoo's sign). `cmc_cost_amount` shows it positive.
- **Runs:** compute from draft; a computed run and its results are locked; *Reset to Draft* deletes
  the results. A closing cannot be reset while a computed costing reads it.
- **Bridges:** declare a profile in `_cmc_bridge_profiles()`; never override `_cmc_auto_classify`.
- System writes use `object()` context sentinels (`CMC_AUTO`, `CMC_GATE`, `CMC_RUN_STATE`), never booleans.

## Tests

```bash
createdb X
odoo-bin -d X --addons-path=odoo/addons,enterprise,<this repo> -i cmc_suite --without-demo=False --stop-after-init
odoo-bin -d X --addons-path=... -u <all 10 modules> --test-enable \
  --test-tags=/cmc_base,/cmc_full_costing,/cmc_direct_costing,/cmc_account,/cmc_hr_timesheet,/cmc_mrp,/cmc_abc,/cmc_joint_costs,/cmc_tdabc,/cmc_suite \
  --stop-after-init
```

`-u` must list every module, otherwise only a couple of tests run.

To upgrade an existing database, run `-u` on **all** cmc modules with `--i18n-overwrite`: without it,
Odoo keeps the Italian terms already in the database.
