"""WP-12 ``cmc_abb`` - activity-based budgeting, spending profiles, capacity time states, driver what-if,
ABC variances. Capabilities: BUD-06, CAP-05, CAP-06, STD-05, FCS-03 (moved from WP-11, AD-118).

Sources: gap register rows BUD-06 / CAP-05 / CAP-06 / STD-05 / FCS-03; Kaplan & Cooper Ch.7 (fundamental
equation, spending / unexpected-utilisation / unused capacity: 212,800 + 56,000 + 11,200 = 280,000), Ch.14
(two-tier rates), Ch.15 (ABB south->north, spending profiles flexible / committed-fixed / committed-step,
truck per 800 shipments 1.6 -> 2.0, first constraining resource, north->south prospective rates); Cokins
Exec Ch.2/6/8 (ABB/P reverse computation, step-fixed lumps, CAM-I time states, unused cost to a responsible
object); Cokins MIW / Frost St.6-8 (FTE = Σ h x volume / annual h, ABC variance actual vs ABC, three-way
actual / ABC / ABB, idle trend). Binding: AD-102 (depends cmc_abc + cmc_budget only), AD-104 (planned
figures on cmc.plan.version), AD-105 (month grain), AD-107 (planned volumes = cmc.measure.quantity facts
with version_id), AD-108 (rate registry), AD-110 (plan figures never analytic lines), AD-112, AD-113 (runs,
snapshots, AD-002 usages), AD-114 (grouped reads, bounded Python), AD-115 (groups, company rules), AD-118
(WP-12 Must; skip: variability-scale UI beyond a selection, bottleneck scheduling).

CONTRACT (whole WP-12; each test file repeats the part it covers)
Figures: costs are POSITIVE figures on every cmc_abb model (as cmc_abc results); variances are positive
when favourable (AD-112 spirit). Budget lines written into cmc_budget keep cmc_budget's own convention
(``figure`` positive, ``amount`` natural sign).

1. ``cmc.abc.resource`` (``_inherit`` in cmc_abb, configuration: manager writes, user reads - existing ACL):
   ``spending_profile`` Selection ``flexible`` / ``committed_fixed`` (default) / ``committed_step``;
   ``unit_size`` Float (default 1.0): resource-driver units one supply unit provides (1,600 h per FTE,
   800 shipments per truck); ``unit_cost`` Monetary (company currency; ``currency_id`` related company):
   cost of one supply unit; ``committed_units`` Float: supply units on hand today (FTE on payroll, trucks
   owned); ``budget_element_id`` M2o cmc.cost.element (check_company) - element of the budget lines,
   fallback = the single element of ``cost_element_ids``; ``idle_responsibility_id`` /
   ``nonproductive_responsibility_id`` M2o cmc.responsibility.center (check_company): who owns the idle /
   the non-productive capacity (CAP-05: sales owns idle, operations owns non-productive).
   ValidationError: ``unit_size`` <= 0; ``unit_cost`` < 0; ``committed_units`` < 0.
   Committed profiles: ``unit_size`` and ``unit_cost`` are ANNUAL; over a span of m months capacity per
   unit = unit_size x m / 12 and cost per unit = unit_cost x m / 12 (fixture spans 12 months: factor 1).
   Flexible: ``unit_cost`` per supply unit, no time scaling (energy: unit_size 1, 0.20 per kWh).
2. ``cmc.budget.line`` (``_inherit``): ``abb_run_id`` M2o cmc.abb.run (index, ondelete cascade) and
   ``abb_resource_id`` M2o cmc.abc.resource: the lines an ABB run wrote.
3. ``cmc.abb.run`` (cmc.run.mixin; company rule, check_company): ``name``, ``company_id``, ``currency_id``
   (related), ``source_version_id`` (cmc.plan.version holding the planned volumes, APPROVED at compute),
   ``target_version_id`` (DRAFT cmc.plan.version receiving the budget lines and the prospective rates;
   optional: empty = no lines, no rates), ``output_measure_id`` (statistical cmc.cost.driver: the planned
   volume measure), ``calibration_run_id`` (computed cmc.abc.run, optional), ``calibration_tolerance``
   Float percent (default 20.0), ``baseline_run_id`` (computed cmc.abb.run, FCS-03), inputs (run-line mixin,
   locked when computed): ``bill_line_ids`` -> ``cmc.abb.bill.line`` (``cost_object_id``, ``activity_id``,
   ``qty_per_unit`` > 0 = activity-driver units per output unit), ``usage_line_ids`` ->
   ``cmc.abb.usage.line`` (``activity_id``, ``resource_id``, ``usage_per_unit`` > 0 = resource-driver units
   per activity-driver unit), ``driver_override_ids`` -> ``cmc.abb.driver.override`` (``activity_id``,
   ``quantity`` >= 0 = planned activity-driver quantity replacing the computed demand: FCS-03),
   ``time_state_ids`` -> ``cmc.abb.time.state`` (``resource_id``, ``mode`` Selection ``standby`` /
   ``maintenance`` / ``changeover`` / ``training``, ``quantity`` > 0 resource-driver units,
   ``responsibility_id`` optional override). All header inputs + those O2m are ``_cmc_input_fields``.
   ``action_prefill_bill()`` (draft, calibration run set, else UserError): replaces the bill lines with, per
   A->CO edge of the calibration run (``line_ids`` with ``activity_id`` and ``cost_object_id``),
   qty_per_unit = edge driver_qty / calibration result ``quantity`` of that object.
   ``action_what_if()`` (computed runs only, else UserError) -> new DRAFT cmc.abb.run: same source, measure,
   calibration, copies of bill / usage / override / time-state lines, ``baseline_run_id`` = self,
   ``target_version_id`` EMPTY (a what-if never writes budget lines nor rates).
   Compute (``action_compute``), span = source version span, m = its months:
   a. UserError: source not approved; target set and not draft; target == source; versions of another
      company (check_company); no planned facts of ``output_measure_id`` in the source version; an activity
      with demand > 0 and no usage line; a used resource without ``driver_id``; a resource to budget without
      budget element. Usages recorded (AD-002) on the source version and the calibration run.
   b. Planned volume per cost object = Σ PLANNED facts (``version_id`` = source) of ``output_measure_id``
      (grouped read, every month of the span; actual facts and other measures ignored).
   c. Activity demand = Σ_objects volume x qty_per_unit (an override replaces it).
   d. Resource demand = Σ_activities activity demand x usage_per_unit; non-productive time (time states) is
      added to what must be supplied: need = demand + Σ non-productive quantity.
   e. Supply by profile: flexible -> supply_units = need / unit_size (no rounding), unused 0;
      committed_step -> supply_units = ceil(need / capacity per unit) (6-digit float_round first: 1,600 / 800
      is exactly 2); committed_fixed -> supply_units = committed_units. supply_qty = supply_units x capacity
      per unit; unused_qty = max(supply_qty - need, 0); required_units = need / capacity per unit (= FTE for
      people); shortage_units = step: max(supply - committed, 0), fixed: max(required - committed, 0),
      flexible 0; excess_units = step: max(committed - supply, 0), else 0. budgeted_cost = supply_units x cost
      per unit. Resource rate = budgeted_cost / max(supply_qty, need) (shortage: no over-assignment); rate 0
      ONLY when that divisor is 0 (RN-001: a committed resource with zero demand keeps rate = cost / supply and
      its whole supply is an idle state of ``idle_responsibility_id``; conservation holds).
   f. North->south: activity rate = Σ usage_per_unit x resource rate; fixed_rate from committed resources,
      variable_rate from flexible ones; assigned_cost = demand x rate. Object unit_cost = Σ qty_per_unit x
      activity rate, amount = volume x unit_cost.
   g. CAP-06 first constraining resource per activity: capacity_qty = min over its COMMITTED resources of
      (supply_qty - non-productive - demand of the OTHER activities on it) / usage_per_unit (fungible
      resources, Kaplan Ch.15); ``constraining_resource_id`` = the argmin. Run ``bottleneck_resource_id`` /
      ``mix_capacity_factor`` = argmin / min over committed resources with demand of (supply_qty -
      non-productive) / demand: how far the planned mix can be scaled before the first resource binds.
   h. Calibration (BUD-06 extrapolation warning): per activity calibration_qty = ``used_qty`` of the
      calibration run activity cost x (m / months of the calibration span); deviation_percent = (demand -
      calibration) / calibration x 100; |deviation| > tolerance (or calibration 0) -> warning ``volume``;
      a planned cost object without a result in the calibration run -> warning ``mix``.
      ``extrapolation_warning`` = any warning.
   i. Target version: per resource ONE ``_cmc_budget_add`` (12 monthly lines, re-summing to budgeted_cost):
      center = resource center, element = budget element, behavior ``fixed`` (committed) / ``variable``
      (flexible), ``driver_id`` = resource driver, ``abb_run_id`` / ``abb_resource_id`` set. Never an
      ``account.analytic.line`` (AD-110). Prospective rates (AD-108, origin = TARGET version: planned, so
      the default ``_cmc_lookup`` without origin never returns them) for every month of the span: holder
      activity x activity ``driver_id`` x total / fixed / variable; holder resource x resource ``driver_id``
      x total; holder cost object x ``output_measure_id`` x total (unit cost). Same-key rows of the target
      are replaced. Publisher (RN-002): ``cmc.rate.abb_run_id`` (M2o cmc.abb.run, index, copy False, added by
      cmc_abb) = the run that wrote the row. Reset / unlink deletes the run's budget lines and ONLY the rates
      with its ``abb_run_id`` - a hand-typed rate of the target is never deleted (target still draft: else
      the version lock raises UserError and nothing is deleted).
      RN-002: at most ONE non-draft run per ``target_version_id`` (Compute of another -> UserError). Budget
      lines with ``abb_run_id`` of a non-draft run: write / unlink by hand -> UserError (users and managers);
      only the engine (Compute / reset / unlink of the run) writes or removes them.
   j. Baseline (FCS-03): ``delta_cost`` = total_budgeted_cost - baseline's; per resource
      ``delta_supply_units`` / ``delta_cost`` vs the baseline resource row; ``predicted_cost`` = Σ demand x
      BASELINE activity rate (forecast driver qty x stable rates); all 0 without a baseline.
   Conservation (fails loud): total_budgeted_cost = total_assigned_cost + total_unused_cost.
   Header outputs: ``total_budgeted_cost``, ``total_assigned_cost``, ``total_unused_cost``,
   ``extrapolation_warning``, ``bottleneck_resource_id``, ``mix_capacity_factor``, ``delta_cost``,
   ``predicted_cost`` (reset clears them).
   Results (cmc.run.result.mixin, ``_cmc_result_fields``, user read-only):
   - ``activity_result_ids`` -> ``cmc.abb.activity.result``: ``activity_id``, ``driver_id``, ``demand_qty``,
     ``calibration_qty``, ``deviation_percent``, ``rate``, ``fixed_rate``, ``variable_rate``,
     ``assigned_cost``, ``capacity_qty``, ``constraining_resource_id``.
   - ``resource_result_ids`` -> ``cmc.abb.resource.result``: ``resource_id``, ``center_id``, ``element_id``,
     ``spending_profile``, ``demand_qty``, ``nonproductive_qty``, ``required_units``, ``supply_units``,
     ``supply_qty``, ``unused_qty``, ``committed_units``, ``shortage_units``, ``excess_units``,
     ``budgeted_cost``, ``assigned_cost``, ``unused_cost``, ``rate``, ``delta_supply_units``, ``delta_cost``.
   - ``object_result_ids`` -> ``cmc.abb.object.result``: ``cost_object_id``, ``planned_qty``, ``unit_cost``,
     ``amount``.
   - ``capacity_state_ids`` -> ``cmc.abb.capacity.state`` (CAP-05): per resource ``state`` ``productive``
     (demand) / ``nonproductive`` (one row per mode) / ``idle`` (supply - need; committed only), ``mode``,
     ``quantity``, ``cost`` = quantity x resource rate, ``responsibility_id`` (non-productive: line override
     else resource ``nonproductive_responsibility_id``; idle: resource ``idle_responsibility_id``; productive
     empty). Σ quantity = supply_qty, Σ cost = budgeted_cost per resource; Σ non-productive + idle cost =
     unused_cost. ``run._cmc_unused_by_responsibility()`` -> {responsibility id or False: cost}.
   - ``warning_ids`` -> ``cmc.abb.warning``: ``warning_type`` ``volume`` / ``mix``, ``activity_id``,
     ``cost_object_id``, ``calibration_qty``, ``planned_qty``, ``deviation_percent``.
4. ``cmc.abb.variance.run`` (cmc.run.mixin, STD-05): ``name``, ``company_id``, ``abb_run_id`` (computed,
   usage recorded), ``date_from``, ``date_to`` (inside the ABB span, else UserError; RN-003 / AD-105: date_from
   first day of a month, date_to last day of a month, else ValidationError); results ``line_ids`` ->
   ``cmc.abb.variance.line`` one per resource of the ABB run. f = months of the span / ABB months.
   actual_demand_qty = Σ_activities ACTUAL facts (``version_id`` False, company, span) of the activity
   ``driver_id`` x usage_per_unit; actual_cost = ledger pool of the resource (cmc_abc pool rule: center x
   elements, no activity-tagged nor stamped-direct lines) in the span, sudo, grouped.
   Committed: static_budget_cost = budget_cost = budgeted_cost x f; supply_qty = supply_qty x f;
   budget_demand_qty = ABB demand x f; rate = ABB resource rate. Flexible: budget_cost = rate x actual
   demand (two-tier, Kaplan Ch.14), static_budget_cost = budgeted_cost x f, supply = actual demand.
   assigned_cost = actual_demand_qty x rate; budgeted_unused_cost = (supply_qty - budget_demand_qty) x rate;
   utilisation_variance = (actual_demand_qty - budget_demand_qty) x rate (negative = capacity planned for
   use left idle); spending_variance = budget_cost - actual_cost; abc_variance = assigned_cost - actual_cost.
   Identity per line (fails loud): assigned + budgeted_unused - utilisation = budget_cost; actual_cost =
   budget_cost - spending_variance. Header totals ``total_budget_cost``, ``total_actual_cost``,
   ``total_assigned_cost``, ``total_budgeted_unused_cost``, ``total_utilisation_variance``,
   ``total_spending_variance``. ``run._cmc_three_way(groupby=("center_id",))`` -> list of dicts with the
   groupby keys + ``actual_cost``, ``abc_cost`` (assigned), ``abb_cost`` (static budget), ``abc_variance``.
   ``env["cmc.abb.variance.run"]._cmc_idle_trend(resources, date_from, date_to)`` -> list of dicts
   {``month`` (date_from of the run), ``resource``, ``unused_cost`` (= budget_cost - assigned_cost),
   ``unused_percent`` (/ budget_cost x 100)} over COMPUTED variance runs of the company, by month.
5. Security (AD-115): ACL user = read/write/create/unlink on the two runs and their input lines, READ only
   on every result model; manager full; non-CMC users nothing. Global ``company_id parent_of company_ids``
   rule on every new model (not noupdate), ``_check_company_auto``. A CMC user computes (engine reads facts,
   ledger and ABC results in sudo inside the run company) and writes the target budget lines.
6. AD-114: ABB compute and variance compute read facts / ledger grouped, create in batches: query count
   independent of the number of cost objects / fact rows.

MAIN FIXTURE (company A; calibration 2026, budget 2027)
  Activities SETUP (setups), MACH (machine hours), SHIP (shipments). Resources:
  TECH Z/[SAL] driver labour h, committed_step, 1,600 h/FTE, 48,000/FTE, committed 3, idle->SALES, np->OPS;
  MACHR Z/[DEP] driver machine h, committed_fixed, 2,000 h/machine, 30,000, committed 1, idle->SALES, np->OPS;
  ENERGY Z/[ENE] driver kWh, flexible, unit 1, 0.20/kWh; TRUCK L/[TRK] driver shipments, committed_step,
  800 shipments/truck, 25,000/truck, committed 1, idle->SALES.
  Calibration ABC run 2026: TECH pool 10,000 (SETUP 20% MACH 60% SHIP 20%), edges SETUP P1 10 P2 20,
  MACH P1 500 P2 1,000, SHIP P1 400 P2 500, units produced P1 1,000 P2 1,000 -> used SETUP 30 MACH 1,500
  SHIP 900; bill per unit P1 0.01 / 0.5 / 0.4, P2 0.02 / 1.0 / 0.5.
  Forecast FC27 (approved): units P1 600 Jan + 600 Jul, P2 800 Mar (distractors: planned MACH 9,999 on the
  machine-hour measure, actual units P1 5,555). Target BUD27 (draft budget 2027).
  Usage: SETUP-TECH 5 h; MACH-TECH 1 h, MACH-MACHR 1 h, MACH-ENERGY 10 kWh; SHIP-TRUCK 1, SHIP-TECH 0.25 h.
  Demand: SETUP 28 (-6.67% vs 30), MACH 1,400 (-6.67%), SHIP 880 (-2.22%) -> no warning.
  TECH 140 + 1,400 + 220 = 1,760 h -> FTE 1.1 -> 2 FTE = 3,200 h, unused 1,440, excess 1, 96,000, rate 30.
  MACHR 1,400 of 2,000, unused 600, 30,000, rate 15. ENERGY 14,000 kWh x 0.20 = 2,800, rate 0.20.
  TRUCK 880 -> 1.1 -> 2 trucks = 1,600, unused 720, shortage 1, 50,000, rate 31.25.
  Total 178,800 = assigned 104,100 + unused 74,700 (TECH 43,200 + MACHR 9,000 + TRUCK 22,500).
  Rates: SETUP 5 x 30 = 150; MACH 30 + 15 + 2 = 47 (F45 V2); SHIP 31.25 + 7.5 = 38.75.
  Assigned SETUP 4,200, MACH 65,800, SHIP 34,100. Unit cost P1 1.5 + 23.5 + 15.5 = 40.5 (48,600),
  P2 3 + 47 + 19.375 = 69.375 (55,500).
  First constraining: SETUP (3,200 - 1,620) / 5 = 316 TECH; MACH min(TECH 2,840, MACHR 2,000) = 2,000
  MACHR; SHIP min(TRUCK 1,600, TECH 6,640) = 1,600 TRUCK. Mix factor min(1.818, 1.4286, 1.818) = 1.4286
  MACHR. Center budget BUD27: Z fixed 126,000 + variable 2,800, L fixed 50,000.
"""

from datetime import date

from odoo.tests.common import TransactionCase, new_test_user

Y26_FROM, Y26_TO = date(2026, 1, 1), date(2026, 12, 31)
Y27_FROM, Y27_TO = date(2027, 1, 1), date(2027, 12, 31)
D_MID = date(2027, 6, 15)


class AbbCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.company_a = cls.env["res.company"].create({"name": "CMC WP12 A"})
        cls.env_a = cls._company_env(cls.company_a)
        env = cls.env_a
        a = cls.company_a
        cls.manager_a = new_test_user(
            cls.env, login="wp12_manager_a", groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=a.id, company_ids=[(6, 0, a.ids)],
        )
        Element = env["cmc.cost.element"]
        cls.sal = Element.create({"name": "WP12 salaries", "code": "WP12SAL", "company_id": a.id})
        cls.dep = Element.create({"name": "WP12 depreciation", "code": "WP12DEP", "company_id": a.id})
        cls.ene = Element.create({"name": "WP12 energy", "code": "WP12ENE", "company_id": a.id})
        cls.trk = Element.create({"name": "WP12 trucks", "code": "WP12TRK", "company_id": a.id})
        cls.z = cls._center(env, "WP12 Z production", a)
        cls.lg = cls._center(env, "WP12 L logistics", a)
        # ---- measures (AD-107): activity drivers, resource drivers, output measure
        cls.setups = cls._driver(env, "WP12SETUP", "transaction")
        cls.mhours = cls._driver(env, "WP12MH", "duration")
        cls.shipments = cls._driver(env, "WP12SHIP", "transaction")
        cls.lhours = cls._driver(env, "WP12LH", "duration")
        cls.kwh = cls._driver(env, "WP12KWH", "intensity")
        cls.units = cls._driver(env, "WP12UNITS", "transaction", kind="statistical")
        Act = env["cmc.abc.activity"]
        cls.process = Act.create({"name": "WP12 Fulfil orders", "code": "WP12PR", "node_type": "process"})
        cls.setup = cls._activity(env, "Set up", "WP12SET", cls.setups)
        cls.mach = cls._activity(env, "Run machines", "WP12MACH", cls.mhours)
        cls.ship = cls._activity(env, "Ship orders", "WP12SHP", cls.shipments)
        # ---- responsibility (CAP-05: operations owns non-productive, sales owns idle)
        Resp = env["cmc.responsibility.center"]
        cls.ops = Resp.create({"name": "WP12 Operations", "owner_id": cls.env.user.id, "company_id": a.id})
        cls.sales = Resp.create({"name": "WP12 Sales", "owner_id": cls.env.user.id, "company_id": a.id})
        cls.maint = Resp.create({"name": "WP12 Maintenance", "owner_id": cls.env.user.id, "company_id": a.id})
        # ---- resources with spending profiles (CAP-06)
        Res = env["cmc.abc.resource"]
        cls.tech = Res.create({
            "name": "WP12 technicians", "code": "WP12TECH", "center_id": cls.z.id,
            "cost_element_ids": [(6, 0, cls.sal.ids)], "driver_basis": "percent", "driver_id": cls.lhours.id,
            "spending_profile": "committed_step", "unit_size": 1600, "unit_cost": 48000, "committed_units": 3,
            "idle_responsibility_id": cls.sales.id, "nonproductive_responsibility_id": cls.ops.id,
        })
        cls.machr = Res.create({
            "name": "WP12 machines", "code": "WP12MACHR", "center_id": cls.z.id,
            "cost_element_ids": [(6, 0, cls.dep.ids)], "driver_basis": "quantity", "driver_id": cls.mhours.id,
            "spending_profile": "committed_fixed", "unit_size": 2000, "unit_cost": 30000, "committed_units": 1,
            "idle_responsibility_id": cls.sales.id, "nonproductive_responsibility_id": cls.ops.id,
        })
        cls.energy = Res.create({
            "name": "WP12 energy", "code": "WP12ENE", "center_id": cls.z.id,
            "cost_element_ids": [(6, 0, cls.ene.ids)], "driver_basis": "quantity", "driver_id": cls.kwh.id,
            "spending_profile": "flexible", "unit_size": 1, "unit_cost": 0.20,
        })
        cls.truck = Res.create({
            "name": "WP12 trucks", "code": "WP12TRUCK", "center_id": cls.lg.id,
            "cost_element_ids": [(6, 0, cls.trk.ids)], "driver_basis": "quantity", "driver_id": cls.shipments.id,
            "spending_profile": "committed_step", "unit_size": 800, "unit_cost": 25000, "committed_units": 1,
            "idle_responsibility_id": cls.sales.id, "nonproductive_responsibility_id": cls.ops.id,
        })
        # ---- cost objects (AD-106)
        Product = cls.env["product.product"]
        cls.p1 = Product.create({"name": "WP12 P1"})
        cls.p2 = Product.create({"name": "WP12 P2"})
        cls.p3 = Product.create({"name": "WP12 P3 (new)"})
        cls.obj_p1, cls.obj_p2, cls.obj_p3 = env["cmc.cost.object"]._cmc_ensure_objects(
            cls.p1 | cls.p2 | cls.p3, company=a
        )
        # ---- calibration: a real cmc_abc v2 run on 2026 (the producer of used_qty / bill of activities)
        cls._cost(env, cls.z, 10000, cls.sal, "fixed", day=date(2026, 6, 15))
        cls._fact(env, cls.units, 1000, cost_object=cls.obj_p1, day=date(2026, 6, 15))
        cls._fact(env, cls.units, 1000, cost_object=cls.obj_p2, day=date(2026, 6, 15))
        cls.calib = env["cmc.abc.run"].create({
            "name": "WP12 calibration 2026", "company_id": a.id, "date_from": Y26_FROM, "date_to": Y26_TO,
            "output_measure_id": cls.units.id,
            "resource_line_ids": [
                (0, 0, {"resource_id": cls.tech.id, "activity_id": cls.setup.id, "percent": 20}),
                (0, 0, {"resource_id": cls.tech.id, "activity_id": cls.mach.id, "percent": 60}),
                (0, 0, {"resource_id": cls.tech.id, "activity_id": cls.ship.id, "percent": 20}),
            ],
            "line_ids": [
                (0, 0, cls._a2o(cls.setup, cls.obj_p1, 10)), (0, 0, cls._a2o(cls.setup, cls.obj_p2, 20)),
                (0, 0, cls._a2o(cls.mach, cls.obj_p1, 500)), (0, 0, cls._a2o(cls.mach, cls.obj_p2, 1000)),
                (0, 0, cls._a2o(cls.ship, cls.obj_p1, 400)), (0, 0, cls._a2o(cls.ship, cls.obj_p2, 500)),
            ],
        })
        cls.calib.action_compute()
        # ---- forecast volumes (AD-104/107: planned facts of an approved version)
        cls.fc27 = cls._version(env, "WP12 FC27", "forecast")
        cls._fact(env, cls.units, 600, cost_object=cls.obj_p1, day=date(2027, 1, 15), version=cls.fc27)
        cls._fact(env, cls.units, 600, cost_object=cls.obj_p1, day=date(2027, 7, 15), version=cls.fc27)
        cls._fact(env, cls.units, 800, cost_object=cls.obj_p2, day=date(2027, 3, 15), version=cls.fc27)
        # distractors: another measure planned on the version, an ACTUAL output fact in the span
        cls._fact(env, cls.mhours, 9999, cost_object=cls.obj_p1, day=date(2027, 2, 15), version=cls.fc27)
        cls._fact(env, cls.units, 5555, cost_object=cls.obj_p1, day=date(2027, 1, 20))
        cls.fc27.with_user(cls.manager_a).action_approve()
        cls.bud27 = cls._version(env, "WP12 BUD27", "budget")

    # ---- builders ---------------------------------------------------------

    @classmethod
    def _company_env(cls, company):
        return cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))

    @classmethod
    def _center(cls, env, name, company):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": company.id}
        )

    @classmethod
    def _driver(cls, env, code, mtype, kind="allocation", company=None):
        company = company or cls.company_a
        return env["cmc.cost.driver"].create({
            "name": "Driver %s" % code, "code": code, "measure_kind": kind, "measure_type": mtype,
            "company_id": company.id,
        })

    @classmethod
    def _activity(cls, env, name, code, driver, parent=None):
        return env["cmc.abc.activity"].create({
            "name": name, "code": code, "node_type": "activity", "parent_id": (parent or cls.process).id,
            "driver_id": driver.id,
        })

    @staticmethod
    def _a2o(activity, cost_object, qty):
        return {"activity_id": activity.id, "cost_object_id": cost_object.id, "driver_id": activity.driver_id.id,
                "driver_qty": qty}

    @classmethod
    def _cost(cls, env, center, cost, element, behavior, day=D_MID):
        return env["account.analytic.line"].create({
            "name": "wp12 cost", "account_id": center.id, "amount": -cost,  # AD-005: cost = negative amount
            "company_id": center.company_id.id, "cmc_cost_element_id": element.id,
            "cmc_behavior": behavior, "date": day,
        })

    @classmethod
    def _fact(cls, env, measure, qty, cost_object=None, day=D_MID, version=None):
        # manual facts are a supported source (AD-107); a feeder writes the very same rows
        return env["cmc.measure.quantity"].create({
            "driver_id": measure.id, "date": day, "company_id": measure.company_id.id,
            "cost_object_id": cost_object.id if cost_object else False, "quantity": qty,
            "version_id": version.id if version else False,
        })

    @classmethod
    def _version(cls, env, name, vtype, company=None, date_from=Y27_FROM, date_to=Y27_TO):
        return env["cmc.plan.version"].create({
            "name": name, "version_type": vtype, "company_id": (company or cls.company_a).id,
            "date_from": date_from, "date_to": date_to,
        })

    # ---- main ABB run -----------------------------------------------------

    def _bill(self):
        return [
            {"cost_object_id": self.obj_p1.id, "activity_id": self.setup.id, "qty_per_unit": 0.01},
            {"cost_object_id": self.obj_p1.id, "activity_id": self.mach.id, "qty_per_unit": 0.5},
            {"cost_object_id": self.obj_p1.id, "activity_id": self.ship.id, "qty_per_unit": 0.4},
            {"cost_object_id": self.obj_p2.id, "activity_id": self.setup.id, "qty_per_unit": 0.02},
            {"cost_object_id": self.obj_p2.id, "activity_id": self.mach.id, "qty_per_unit": 1.0},
            {"cost_object_id": self.obj_p2.id, "activity_id": self.ship.id, "qty_per_unit": 0.5},
        ]

    def _usage(self):
        return [
            {"activity_id": self.setup.id, "resource_id": self.tech.id, "usage_per_unit": 5},
            {"activity_id": self.mach.id, "resource_id": self.tech.id, "usage_per_unit": 1},
            {"activity_id": self.mach.id, "resource_id": self.machr.id, "usage_per_unit": 1},
            {"activity_id": self.mach.id, "resource_id": self.energy.id, "usage_per_unit": 10},
            {"activity_id": self.ship.id, "resource_id": self.truck.id, "usage_per_unit": 1},
            {"activity_id": self.ship.id, "resource_id": self.tech.id, "usage_per_unit": 0.25},
        ]

    def _abb_run(self, compute=True, env=None, bill=None, usage=None, overrides=(), time_states=(), **vals):
        env = env or self.env_a
        run = env["cmc.abb.run"].create({
            "name": "WP12 ABB 2027",
            "company_id": self.company_a.id,
            "source_version_id": self.fc27.id,
            "target_version_id": self.bud27.id,
            "output_measure_id": self.units.id,
            "calibration_run_id": self.calib.id,
            "bill_line_ids": [(0, 0, v) for v in (self._bill() if bill is None else bill)],
            "usage_line_ids": [(0, 0, v) for v in (self._usage() if usage is None else usage)],
            "driver_override_ids": [(0, 0, v) for v in overrides],
            "time_state_ids": [(0, 0, v) for v in time_states],
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _res(run, resource):
        return run.resource_result_ids.filtered(lambda r: r.resource_id == resource)

    @staticmethod
    def _act(run, activity):
        return run.activity_result_ids.filtered(lambda r: r.activity_id == activity)

    @staticmethod
    def _obj(run, cost_object):
        return run.object_result_ids.filtered(lambda r: r.cost_object_id == cost_object)
