"""WP-07 - shared fixture of the ``cmc_abc`` v2 multistage ABC red tests (ABC-01..ABC-11, INT-02).

Sources: gap register rows ABC-01..11 / INT-02; Kaplan & Cooper Ch.6-7, Ch.14 (practical capacity,
two-tier rates); Cokins MIW ch.4 (multistage network, conservation), Frost St.6 (idle burden
90 + 13.5 + 13.5 = 117); Turney Ch.5 (bill of costs / bill of activities); AD-101, AD-106..109,
AD-113, AD-114, AD-115, AD-118 (WP-07 scope: acyclic network only, cycles fail loud).

CONTRACT (whole WP-07; each test file repeats the part it covers)
Master data
- ``cmc.abc.activity`` (v1 model, extended) inherits ``cmc.attribute.mixin`` and the shipped cmc_base
  attributes VA_CLASS / COQ / EFFICIENCY list ``cmc.abc.activity`` in ``target_model_ids`` (cmc_abc data).
  New fields: ``node_type`` Selection ``process`` / ``activity`` / ``task`` (default ``activity``);
  ``parent_id`` M2o cmc.abc.activity (check_company) + ``child_ids`` O2m; ``cost_level`` Selection
  ``unit`` / ``batch`` / ``product`` / ``customer`` / ``facility`` (optional); ``owner_id`` M2o res.users;
  ``is_sustaining`` Boolean stored, computed ``cost_level == 'facility'``, readonly=False (configurable:
  an SG&A activity can be marked by hand).
  ValidationError: a process with a parent; an activity whose parent is not a process; a task whose
  parent is not an activity (the three-level typing makes a cycle impossible); ``driver_id`` of kind ``statistical`` (AD-107 inv.3);
  ``cost_level`` and ``driver_id.cmc_abc_level`` both set and different (ABC-05 level matching).
- ``cmc.cost.driver`` (_inherit in cmc_abc, AD-107): ``cmc_abc_level`` Selection (same 5 levels);
  ``cmc_component_ids`` O2m ``cmc.abc.driver.component`` (``driver_id``, ``component_driver_id`` M2o
  cmc.cost.driver required, ``weight`` Float): weighted index driver, qty = Σ weight x component qty.
  Driver type = cmc_base ``measure_type`` (transaction / duration / intensity), snapshotted on results.
- ``cmc.cost.object.object_type`` gains ``channel`` (res_model ``cmc.attribute.value``), ``order`` and
  ``sustaining`` (res_model ``res.company``, res_id = company: the business-sustaining object).
- ``cmc.abc.resource`` (ABC-02, company rule parent_of, manager configures, user reads): ``name``,
  ``code`` (unique per company), ``company_id`` required, ``center_id`` M2o analytic account required,
  ``cost_element_ids`` M2m cmc.cost.element (empty = every element of the center), ``driver_basis``
  Selection ``percent`` (default, survey %) / ``quantity`` (measured usage), ``driver_id`` (optional unit).
  ValidationError when two active resources of a company overlap on (center, element) (double count).
  Pool = cost (positive, AD-005) of the cost-base lines of center x elements in the run span/company,
  split fixed / variable (variable = ``cmc_behavior == 'variable'``, everything else fixed), EXCLUDING
  lines tagged with ``cmc_activity_id`` (direct charge, v1 stage 1) and stamped direct lines
  (``cmc_directness = 'direct'`` with ``cmc_cost_object_id``); read grouped (AD-111/114).
Run inputs (``cmc.abc.run``, all in ``_cmc_input_fields``, locked when computed)
- ``resource_line_ids`` O2m ``cmc.abc.resource.line`` (run.line mixin): ``resource_id``, ``activity_id``
  (must be a node_type ``activity``: ValidationError), ``percent`` or ``quantity`` per the resource basis.
  Compute: a percent resource whose lines do not sum to 100 (+-0.01) -> UserError naming it.
- ``line_ids`` (v1 ``cmc.abc.line``, extended) = network edges, exactly one sender and one receiver:
  sender ``activity_id`` or ``source_object_id`` (CO->CO); receiver ``target_activity_id`` (A->A),
  ``cost_object_id`` (A->CO, CO->CO) or v1 ``product_id`` (cost object of the product in the run company,
  stored on ``cost_object_id``). ``driver_id``, ``driver_qty`` (> 0), optional ``output_units`` +
  ``batch_size`` -> ``driver_qty`` = ceil(units / batch size) (computed, store, readonly=False).
- ``capacity_line_ids`` O2m ``cmc.abc.capacity``: ``activity_id``, ``practical_capacity`` (driver units).
- Header: ``trace_direct`` Boolean (default False = v1), ``output_measure_id`` (statistical measure: units
  per cost object from ACTUAL facts of the span), ``traditional_basis`` Selection False / ``volume`` /
  ``rate``, ``traditional_driver_id``, ``traditional_version_id`` (approved cmc.plan.version),
  ``compute_burdens`` Boolean.
- ``action_cmc_prefill_lines()`` (draft, dates required; INT-02): refreshes the feeders of the span
  (``cmc.measure.quantity._cmc_refresh_feeders``), then for each non-sustaining ``activity`` node of the
  company with an allocation ``driver_id`` writes A->CO lines from the ACTUAL facts of the span per cost
  object (index drivers: Σ weight x component facts), replacing the A->CO lines of those activities.
Engine (``action_compute``; acyclic network, AD-118)
- Stage 1: activity cost = Σ resource shares (percent or quantity pro rata, ``spread``) + v1 tag pools.
- A->A in topological order; a cycle (A->A or CO->CO) -> UserError. Each sender distributes 100%.
- Rates: practical capacity line -> ``fixed_rate`` = fixed cost / practical capacity, ``variable_rate``
  = variable cost / used qty (two-tier, Kaplan Ch.14), unused = (capacity - used) x fixed rate (reported,
  never charged); without capacity line rate = cost / used qty. Used > capacity -> UserError.
  RN-003: a capacity line with ZERO usage in the span (no edge) is idle capacity, not an unallocated
  pool: its whole fixed cost -> ``unused_cost`` / flow ``unused`` / ``unused_capacity_cost`` (and so
  into ``idle_burden_rate``), never into ``unallocated_amount``.
- Traditional comparison (ABC-11, RN-001, Kaplan & Cooper zero-sum distortion): compared objects =
  results with a traditional figure; volume pool = the overhead ABC actually assigns to THOSE objects
  (Σ amount - traced over them: unused capacity, sustaining and customer/channel costs that never reach
  them are excluded), so Σ traditional = Σ ABC over the compared objects. With ``compute_burdens`` the
  comparison is on ``full_cost`` and the pool includes the same burdens (Σ full_cost - traced).
- Sustaining (``is_sustaining``) activities: 100% to ``run.sustaining_object_id``; an edge from one ->
  UserError. Cost of an activity with no edge and not sustaining = unallocated (v1 behaviour).
- ``trace_direct``: stamped direct lines go straight to their cost object (``traced_cost``).
- F/V carried proportionally through every flow (fixed + variable = amount everywhere).
- Rates published to ``cmc.rate`` when ``date_from`` is set (month of date_from, origin = run): holder
  activity, driver = activity ``driver_id``, components total/fixed/variable; holder product cost
  object, driver = ``output_measure_id``, unit costs total/fixed/variable. ``traditional_basis = 'rate'``
  records ``cmc.run.usage`` of the version.
Run outputs: ``total_resource_cost`` (resources + tag pools), ``total_activity_cost`` (Σ stage 1),
  ``total_traced_cost``, ``total_object_cost`` (Σ amount - forwarded over objects), ``unused_capacity_cost``,
  ``sustaining_cost``, ``unallocated_amount`` (v1 field), ``in_scope_cost`` (cost base of the resource
  centers in span, every element), ``unassigned_cost`` (= in_scope_cost - the part of it that entered the
  model as resource pool, tag pool or traced cost: reported, never charged), ``idle_burden_rate`` / ``sustaining_burden_rate`` (percent of Σ final ABC),
  ``sustaining_object_id``. Conservation (fails loud): total_resource_cost + total_traced_cost =
  total_object_cost + unused_capacity_cost + sustaining_cost + unallocated_amount.
Results (``cmc.run.result.mixin``, in ``_cmc_result_fields``, deleted by reset)
- ``activity_cost_ids`` -> ``cmc.abc.activity.cost``: ``activity_id``, ``cost_level``, ``driver_id``,
  ``driver_type``, ``resource_cost`` (stage 1), ``received_cost`` (A->A in), ``total_cost``,
  ``fixed_cost``, ``variable_cost``, ``used_qty``, ``practical_capacity``, ``rate``, ``fixed_rate``,
  ``variable_rate``, ``charged_cost``, ``unused_cost``.
- ``flow_ids`` -> ``cmc.abc.flow`` (drill-back of every path): ``stage`` Selection ``resource`` /
  ``tag`` / ``trace`` / ``activity`` / ``object`` / ``unused`` / ``sustaining`` / ``unallocated``;
  ``source_resource_id``, ``cost_element_id``, ``source_activity_id``, ``source_object_id``,
  ``target_activity_id``, ``target_object_id``, ``driver_qty``, ``rate``, ``amount``, ``fixed_amount``,
  ``variable_amount``.
- ``result_ids`` (v1 ``cmc.abc.result``, extended; ``product_id`` optional): ``cost_object_id``,
  ``traced_cost``, ``activity_cost``, ``object_cost`` (CO->CO in), ``amount`` (= sum), ``forwarded_amount``,
  ``fixed_amount``, ``variable_amount``, ``quantity``, ``unit_cost``, ``traditional_cost``,
  ``distortion_percent`` ((ABC - trad) / trad x 100), ``costing_flag`` ``over`` (trad > ABC) / ``under`` /
  ``aligned`` (|distortion| < 1), ``idle_burden``, ``sustaining_burden``, ``full_cost``.
Drill-back helpers: ``run._cmc_bill_of_activities(cost_object)`` -> list of dicts ``{kind: trace|activity|
  object, activity, source_object, driver_qty, rate, amount}`` (Σ = result amount);
  ``run._cmc_bill_of_costs(activity)`` -> list of dicts ``{kind: resource|tag|activity, resource,
  cost_element, source_activity, amount, fixed_amount, variable_amount}`` (Σ = activity total);
  record keys not applicable to a row are EMPTY recordsets of their model, never False;
  ``run._cmc_process_costs()`` -> {process id: Σ stage-1 cost of its activities};
  ``run._cmc_cost_by_level()`` -> {level or False: Σ stage-1}; ``run._cmc_activity_cost_by_attribute(attr)``
  -> {value id or False: Σ stage-1} (mixin ``_cmc_group_by_attribute``).
Backward compatibility (AD-101): every v1 test in test_abc.py / test_reclass.py stays valid unchanged.

MAIN FIXTURE (company A, March 2027; cost = positive figure, stored negative)
  Lines: Z SAL fixed 6000, Z SAL variable 4000, Z DEP fixed 2000, Z SAL fixed 500 tagged QC,
  Z MP variable direct 300 -> P1, Z MP variable direct 200 -> P2, S SAL fixed 3000, S OTH fixed 1000;
  distractor Z SAL fixed 9999 on 2027-04-02.
  Resources: R1 Z/[SAL] 10000 (F6000 V4000) %: SETUP 30 MACH 50 QC 20; R2 Z/[DEP] 2000 qty: MACH 80
  SETUP 20; R3 S/[all] 4000 %: ADMIN 25 CUST 25 MGMT 50.
  Stage 1: SETUP 3400 (F2200 V1200), MACH 6600 (F4600 V2000), QC 2000+500 tag = 2500 (F1700 V800),
  ADMIN 1000, CUST 1000, MGMT 2000 (facility) = 16500. A->A ADMIN -> SETUP 1 / MACH 3 (rate 250):
  SETUP 3650 (F2450), MACH 7350 (F5350).
  Rates: SETUP setups P1 6 P2 4 -> 365 (F245 V120); MACH hours P1 60 P2 40, practical 125 -> fixed
  42.8 + variable 20 = 62.8, unused 25 x 42.8 = 1070; QC inspections P1 3 P2 2 -> 500 (F340 V160);
  CUST orders C1 7 C2 3 -> 100. MGMT -> sustaining 2000.
  Objects: P1 = 300 + 2190 + 3768 + 1500 = 7758 (F5058 V2700); P2 = 200 + 1460 + 2512 + 1000 = 5172
  (F3372 V1800); C1 700; C2 300. Σ objects 13930 + unused 1070 + sustaining 2000 = 17000 = 16500 + 500.
  Units produced P1 100 P2 50 -> unit 77.58 (F50.58 V27) / 103.44 (F67.44 V36). DLH P1 75 P2 25.
"""

from datetime import date

from odoo.tests.common import TransactionCase

D_FROM = date(2027, 3, 1)
D_TO = date(2027, 3, 31)
D_MID = date(2027, 3, 15)


class Abc2Case(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")
        cls.company_a = cls.env["res.company"].create({"name": "CMC WP07 A"})
        cls.env_a = cls._company_env(cls.company_a)
        env = cls.env_a
        Element = env["cmc.cost.element"]
        cls.sal = Element.create({"name": "WP07 salaries", "code": "WP07SAL", "company_id": cls.company_a.id})
        cls.dep = Element.create({"name": "WP07 depreciation", "code": "WP07DEP", "company_id": cls.company_a.id})
        cls.oth = Element.create({"name": "WP07 other", "code": "WP07OTH", "company_id": cls.company_a.id})
        cls.z = cls._center(env, "WP07 Z production", cls.company_a)
        cls.s = cls._center(env, "WP07 S admin", cls.company_a)
        # ---- drivers (AD-107 catalogue; level/type on the driver)
        cls.setups = cls._driver(env, "WP07SETUP", "transaction", level="batch")
        cls.mhours = cls._driver(env, "WP07MH", "duration", level="unit")
        cls.inspections = cls._driver(env, "WP07INSP", "transaction", level="batch")
        cls.orders = cls._driver(env, "WP07ORD", "transaction", level="customer")
        cls.tickets = cls._driver(env, "WP07TCK", "transaction")
        cls.dlh = cls._driver(env, "WP07DLH", "duration")
        cls.produced = cls._driver(env, "WP07UNITS", "transaction", kind="statistical")
        # ---- activity dictionary: process > activity > task
        Act = env["cmc.abc.activity"]
        cls.pr_mfg = Act.create({"name": "Manufacture", "code": "WP07PMFG", "node_type": "process"})
        cls.pr_sup = Act.create({"name": "Support the business", "code": "WP07PSUP", "node_type": "process"})
        cls.setup = cls._activity("Set up machines", "WP07SET", cls.pr_mfg, "batch", cls.setups)
        cls.mach = cls._activity("Run machines", "WP07MACH", cls.pr_mfg, "unit", cls.mhours)
        cls.qc = cls._activity("Inspect products", "WP07QC", cls.pr_mfg, "batch", cls.inspections)
        cls.admin = cls._activity("Support IT", "WP07ADM", cls.pr_sup, False, cls.tickets)
        cls.cust = cls._activity("Serve customers", "WP07CUST", cls.pr_sup, "customer", cls.orders)
        cls.mgmt = cls._activity("Manage the business", "WP07MGMT", cls.pr_sup, "facility", False)
        cls.load = Act.create({"name": "Load machine", "code": "WP07LOAD", "node_type": "task", "parent_id": cls.mach.id})
        # ---- cost objects (AD-106)
        Product = cls.env["product.product"]
        cls.p1 = Product.create({"name": "WP07 P1"})
        cls.p2 = Product.create({"name": "WP07 P2"})
        cls.c1 = cls.env["res.partner"].create({"name": "WP07 Customer 1"})
        cls.c2 = cls.env["res.partner"].create({"name": "WP07 Customer 2"})
        Obj = env["cmc.cost.object"]
        cls.obj_p1, cls.obj_p2 = Obj._cmc_ensure_objects(cls.p1 | cls.p2, company=cls.company_a)
        cls.obj_c1, cls.obj_c2 = Obj._cmc_ensure_objects(cls.c1 | cls.c2, company=cls.company_a, object_type="customer")
        # ---- ledger (analytic) lines of March
        cls._cost(env, cls.z, 6000, cls.sal, "fixed")
        cls._cost(env, cls.z, 4000, cls.sal, "variable")
        cls._cost(env, cls.z, 2000, cls.dep, "fixed")
        cls._cost(env, cls.z, 500, cls.sal, "fixed", activity=cls.qc)
        cls._cost(env, cls.z, 300, cls.mp, "variable", cost_object=cls.obj_p1)
        cls._cost(env, cls.z, 200, cls.mp, "variable", cost_object=cls.obj_p2)
        cls._cost(env, cls.s, 3000, cls.sal, "fixed")
        cls._cost(env, cls.s, 1000, cls.oth, "fixed")
        cls._cost(env, cls.z, 9999, cls.sal, "fixed", day=date(2027, 4, 2))
        # ---- resources (ABC-02)
        Res = env["cmc.abc.resource"]
        cls.r1 = Res.create({"name": "Z people", "code": "WP07R1", "center_id": cls.z.id,
                             "cost_element_ids": [(6, 0, cls.sal.ids)], "driver_basis": "percent"})
        cls.r2 = Res.create({"name": "Z machines", "code": "WP07R2", "center_id": cls.z.id,
                             "cost_element_ids": [(6, 0, cls.dep.ids)], "driver_basis": "quantity",
                             "driver_id": cls.mhours.id})
        cls.r3 = Res.create({"name": "S admin", "code": "WP07R3", "center_id": cls.s.id, "driver_basis": "percent"})
        # ---- measure facts (actual, March)
        cls._fact(env, cls.produced, cls.obj_p1, 100)
        cls._fact(env, cls.produced, cls.obj_p2, 50)
        cls._fact(env, cls.produced, cls.obj_p1, 999, day=date(2027, 4, 1))
        cls._fact(env, cls.dlh, cls.obj_p1, 75)
        cls._fact(env, cls.dlh, cls.obj_p2, 25)

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
    def _driver(cls, env, code, mtype, level=False, kind="allocation", company=None):
        company = company or cls.company_a
        return env["cmc.cost.driver"].create({
            "name": "Driver %s" % code, "code": code, "measure_kind": kind, "measure_type": mtype,
            "cmc_abc_level": level, "company_id": company.id,
        })

    @classmethod
    def _activity(cls, name, code, parent, level, driver, env=None):
        return (env or cls.env_a)["cmc.abc.activity"].create({
            "name": name, "code": code, "node_type": "activity", "parent_id": parent.id if parent else False,
            "cost_level": level, "driver_id": driver.id if driver else False,
        })

    @classmethod
    def _cost(cls, env, center, cost, element, behavior, day=D_MID, activity=None, cost_object=None):
        vals = {
            "name": "wp07 cost", "account_id": center.id, "amount": -cost,  # AD-005: cost = negative amount
            "company_id": center.company_id.id, "cmc_cost_element_id": element.id,
            "cmc_behavior": behavior, "date": day,
        }
        if activity:
            vals["cmc_activity_id"] = activity.id
        if cost_object:
            vals.update(cmc_cost_object_id=cost_object.id, cmc_directness="direct")
        return env["account.analytic.line"].create(vals)

    @classmethod
    def _fact(cls, env, measure, cost_object, qty, day=D_MID, version=None):
        # manual facts are a supported source (AD-107); a feeder writes the very same rows
        return env["cmc.measure.quantity"].create({
            "driver_id": measure.id, "date": day, "company_id": measure.company_id.id,
            "cost_object_id": cost_object.id, "quantity": qty, "version_id": version.id if version else False,
        })

    # ---- main run ---------------------------------------------------------

    def _resource_lines(self):
        return [
            {"resource_id": self.r1.id, "activity_id": self.setup.id, "percent": 30},
            {"resource_id": self.r1.id, "activity_id": self.mach.id, "percent": 50},
            {"resource_id": self.r1.id, "activity_id": self.qc.id, "percent": 20},
            {"resource_id": self.r2.id, "activity_id": self.mach.id, "quantity": 80},
            {"resource_id": self.r2.id, "activity_id": self.setup.id, "quantity": 20},
            {"resource_id": self.r3.id, "activity_id": self.admin.id, "percent": 25},
            {"resource_id": self.r3.id, "activity_id": self.cust.id, "percent": 25},
            {"resource_id": self.r3.id, "activity_id": self.mgmt.id, "percent": 50},
        ]

    def _edges(self):
        def a2o(act, obj, qty):
            return {"activity_id": act.id, "cost_object_id": obj.id, "driver_id": act.driver_id.id, "driver_qty": qty}

        return [
            {"activity_id": self.admin.id, "target_activity_id": self.setup.id, "driver_id": self.tickets.id, "driver_qty": 1},
            {"activity_id": self.admin.id, "target_activity_id": self.mach.id, "driver_id": self.tickets.id, "driver_qty": 3},
            a2o(self.setup, self.obj_p1, 6), a2o(self.setup, self.obj_p2, 4),
            a2o(self.mach, self.obj_p1, 60), a2o(self.mach, self.obj_p2, 40),
            a2o(self.qc, self.obj_p1, 3), a2o(self.qc, self.obj_p2, 2),
            a2o(self.cust, self.obj_c1, 7), a2o(self.cust, self.obj_c2, 3),
        ]

    def _main_run(self, compute=True, env=None, resource_lines=None, edges=None, capacity=None, **vals):
        env = env or self.env_a
        run = env["cmc.abc.run"].create({
            "name": "WP07 main",
            "company_id": self.company_a.id,
            "date_from": D_FROM,
            "date_to": D_TO,
            "trace_direct": True,
            "output_measure_id": self.produced.id,
            "resource_line_ids": [(0, 0, v) for v in (self._resource_lines() if resource_lines is None else resource_lines)],
            "line_ids": [(0, 0, v) for v in (self._edges() if edges is None else edges)],
            "capacity_line_ids": [(0, 0, v) for v in (
                [{"activity_id": self.mach.id, "practical_capacity": 125}] if capacity is None else capacity
            )],
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _res(run, cost_object):
        return run.result_ids.filtered(lambda r: r.cost_object_id == cost_object)

    @staticmethod
    def _act(run, activity):
        return run.activity_cost_ids.filtered(lambda r: r.activity_id == activity)
