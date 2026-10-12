"""WP-16 ``cmc_target_lcc`` - target, kaizen and feature costing, life-cycle costing (TGT-01, TGT-02, LCC-01, LCC-02).

Sources: gap register rows TGT-01/02, LCC-01/02; Kaplan-Cooper Ch5 (kaizen 0.94 x 230,890), Ch11 App (allowable
cost, cardinal rule, component/function targets, supplier target price); Turney Ch8-9 (target = price - profit -
distribution, design guidelines SMT 1.0x / robot 3x / hand 6x, LC account $20,000 / 10,000 = $2 recomputed on
residuals); Emblemsvag LCC Ch1-2, Ch5-8 (phases incl. take-back/disposal, categories usual/hidden/liability/
less tangible, NPV incl. 0%, real vs nominal, liability EMV, committed vs incurred, cost view + cash view, baseline).
Architecture: AD-101 (new module), AD-102 (depends ``cmc_base`` ONLY: rates are READ from ``cmc.rate``, any
producer), AD-106 (product = ``cmc.cost.object``), AD-108 (rate registry), AD-113 (every engine is a run),
AD-114 (set-based), AD-115 (security), AD-118 (WP-16 must), AD-119 (``cmc_utils.npv/real_rate/nominal_rate``).

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Module ``cmc_target_lcc``, depends ``["cmc_base"]`` only. Every model below has ``company_id`` (lines/results:
related to the run, stored), ``_check_company_auto = True`` on the runs, Monetary fields with ``currency_id``
related to the company, and a global ``ir.rule`` ``['|', ('company_id', '=', False), ('company_id', 'parent_of',
company_ids)]`` outside noupdate. Runs inherit ``cmc.run.mixin`` (compute from draft, locked when done, reset
deletes results and clears header outputs); input lines inherit ``cmc.run.line.mixin`` (ondelete cascade);
result rows inherit ``cmc.run.result.mixin``. Header outputs are written by Compute only (a user write ->
UserError) and cleared by reset. Rates are read with the semantics of ``cmc.rate._cmc_lookup`` (run-published
rates only, plan-version rates are never actuals; the latest published of the month wins; company of the run),
in ONE batch per Compute; every origin read is recorded with ``_cmc_record_usage`` (AD-002: the producer cannot
be reset while the consumer is computed). A missing rate -> UserError naming the holder/product.

ACL: runs (``cmc.target.cost``, ``cmc.kaizen.target``, ``cmc.feature.costing``, ``cmc.lcc.model``):
group_cmc_user read/write/create (NO unlink), group_cmc_manager full; input lines: user and manager full;
results: user READ only, manager full; ``cmc.design.guideline`` (configuration): user read, manager full;
internal users without a CMC group: nothing (AccessError).

TGT-01 ``cmc.target.cost`` - target costing sheet of one product
- Inputs (``_cmc_input_fields``): ``name``, ``company_id``, ``product_id`` (product.product, required),
  ``unit_driver_id`` (cmc.cost.driver, required: the output measure the unit cost is published per),
  ``date`` (required, month of the current cost), ``target_price``, ``target_margin_pct`` (percent of the price,
  0 <= m < 100 else ValidationError), ``non_mfg_cost`` (distribution / non-manufacturing cost per unit, default 0),
  ``assembly_cost`` (part of the allowable cost kept at product level, NOT deployed; default 0),
  ``function_ids`` / ``component_ids`` / ``matrix_ids`` (input lines, ``_cmc_run_field = "target_id"``).
- ``cmc.target.cost.function``: ``target_id``, ``name``, ``importance_pct`` (customer weight, percent).
- ``cmc.target.cost.component``: ``target_id``, ``name``, ``product_id`` (optional), ``cost_source``
  ``manual`` (default, uses ``current_cost``) / ``registry`` (unit cost of the component product's cost object,
  driver ``unit_driver_id``, month of ``date``), ``current_cost`` Monetary, ``purchased`` Boolean, ``partner_id``
  (supplier).
- ``cmc.target.cost.matrix`` (function-cost matrix): ``target_id``, ``component_id``, ``function_id``,
  ``share_pct`` = share of the component's cost that serves the function.
- Header outputs: ``cost_object_id`` (the product's ``cmc.cost.object``, ensured at Compute), ``allowable_cost``
  = price x (1 - margin/100) - non_mfg_cost; ``deployable_cost`` = allowable - assembly_cost; ``current_cost`` =
  the registry unit cost (component ``total``) of the product cost object for ``unit_driver_id`` in the month of
  ``date`` (any producer run); ``cost_gap`` = current - allowable; ``reduction_pct`` = gap / current x 100
  (digits (16, 2)); ``target_met`` = gap <= 0.
- Deployment (when functions exist; rules checked at Compute, UserError): sum of ``importance_pct`` = 100;
  sum of ``share_pct`` of each component = 100 (both at a 0.01 tolerance); every function has some cost. Function current cost =
  sum(component cost x share); function target = deployable x importance (``spread``, the targets re-sum to
  deployable to the cent); component importance = sum_f importance_f x (component cost in f / function cost f);
  component target = deployable x component importance (``spread``: sum of component targets = deployable,
  the cardinal rule). ``cost_pct`` = cost / total component cost x 100; ``value_index`` = importance_pct /
  cost_pct on the UNROUNDED percentages (digits (16, 4)); ``gap`` = current_cost - target_cost; ``supplier_target_price`` = target_cost on
  purchased components (0 otherwise).
- Results ``result_ids`` -> ``cmc.target.cost.result``: ``target_id``, ``line_type`` ``function`` /
  ``component``, ``function_id``, ``component_id``, ``partner_id``, ``importance_pct`` (16, 2), ``current_cost``,
  ``cost_pct`` (16, 2), ``target_cost``, ``gap``, ``value_index`` (16, 4), ``supplier_target_price``.

TGT-01 ``cmc.kaizen.target`` - periodic cost-reduction target
- Inputs: ``name``, ``company_id``, ``basis`` ``unit`` (default) / ``flexible``, ``reduction_pct``.
  unit: ``product_id``, ``unit_driver_id``, ``base_date`` (month of the baseline actual unit cost),
  ``date_from`` / ``date_to`` (months tracked); flexible (Kaplan Ch5): ``fixed_budget``, ``variable_budget``
  (flexible variable budget at actual mix), ``actual_cost``.
- Outputs unit: ``base_unit_cost`` (registry rate at base_date; missing -> UserError), ``target_unit_cost`` =
  base x (1 - r/100); per month of the span WITH a published rate one ``cmc.kaizen.target.line`` (result):
  ``date``, ``actual_unit_cost``, ``achieved_pct`` = (base - actual) / base x 100 (16, 2), ``variance`` = target -
  actual (positive = favourable, AD-112), ``met`` = actual <= target; months without a rate get no line; header
  ``achieved_pct`` / ``variance`` / ``target_met`` = those of the LAST month with a line.
  Outputs flexible: ``target_total`` = (1 - r/100) x (fixed + variable), ``variance`` = target_total -
  actual_cost, ``target_met``.

TGT-02 ``cmc.feature.costing`` - design alternatives costed from driver rates
- Inputs: ``name``, ``company_id``, ``date`` (rate month), ``target_cost_id`` (optional, must be computed:
  its ``allowable_cost`` is the bar; usage recorded), ``alternative_ids`` -> ``cmc.feature.alternative``
  (``costing_id``, ``name``), ``variable_ids`` -> ``cmc.feature.variable`` (``costing_id``, ``alternative_id``
  required, ``name``, ``holder_model`` + ``holder_id`` (any rate holder), ``driver_id`` required, ``quantity``,
  ``guideline_id`` optional; readonly ``rate_value`` (16, 6) and ``amount`` written by Compute).
- ``cmc.design.guideline`` (master data): ``name``, ``company_id``, ``driver_id`` required, ``max_quantity``
  (0 = no limit), ``relative_factor`` (default 1.0: SMT 1.0x, robot 3x, hand 6x), ``note``, ``active``.
- Rule: ``rate_value`` = registry rate (holder, driver, date) x guideline.relative_factor (1 without guideline);
  ``amount`` = quantity x rate_value. Results ``result_ids`` -> ``cmc.feature.result`` (``alternative_id``,
  ``estimated_cost``, ``allowable_cost``, ``gap`` = estimated - allowable, ``meets_target`` (True when no target
  and gap <= 0 otherwise), ``violation_count``, ``rank`` 1 = cheapest); ``violation_ids`` ->
  ``cmc.feature.violation`` (``alternative_id``, ``guideline_id``, ``quantity``, ``max_quantity``, ``excess``):
  one per (alternative, active company guideline with max_quantity > 0) whose summed driver quantity exceeds it.
  Header ``best_alternative_id`` = rank 1.

LCC-01 / LCC-02 ``cmc.lcc.model`` - life-cycle cost and profit of a product or customer
- Inputs: ``name``, ``company_id``, ``product_id`` / ``partner_id`` (at least one, ValidationError),
  ``start_year`` Integer, ``horizon_years`` Integer (>= 1, ValidationError), ``discount_rate`` /
  ``inflation_rate`` (percent, 0 allowed), ``rate_basis`` / ``flows_basis`` ``nominal`` | ``real`` (default
  ``real`` both), ``rate_date`` (month of the driver rates), ``previous_id`` (computed model whose unit cost was in
  force: residual recalculation), ``baseline_id`` (computed alternative / do-nothing model: incremental view).
- ``year_ids`` -> ``cmc.lcc.year`` (``model_id``, ``year`` 0..horizon-1, ``volume``, ``price_unit``, ``is_actual``;
  unique (model, year) -> IntegrityError). A horizon year without a line = volume 0, price 0.
- ``cost_line_ids`` -> ``cmc.lcc.cost.line`` (``model_id``, ``name``, ``phase`` ``rd`` / ``production`` /
  ``distribution`` / ``use`` / ``take_back`` (take-back and disposal), ``category`` ``usual`` / ``hidden`` /
  ``liability`` / ``less_tangible``, ``recurrence`` ``recurring`` (default) / ``non_recurring``, ``year``,
  ``amount`` (estimate), ``probability_pct`` (default 100: a contingent liability = EMV), ``actual_amount``,
  ``actual_zero`` Boolean (RN-002: the actual of the line is a CONFIRMED zero, default False),
  ``commit_year`` (year the cost is committed, default 0), driver-based: ``holder_model`` + ``holder_id`` +
  ``driver_id`` + ``quantity`` (amount = quantity x registry rate at ``rate_date``; readonly ``rate_value``
  written by Compute). A line year outside 0..horizon-1 -> UserError at Compute.
- Effective cost of a line: in an ``is_actual`` year = ``actual_amount`` (what happened, no probability); else
  (driver amount or ``amount``) x probability_pct / 100.
- RN-002 (no silent zero): in an ``is_actual`` year a line with a non-zero estimate (``amount``, or the driver
  amount) and ``actual_amount`` 0 is an actual never typed -> UserError at Compute naming the line(s), unless its
  ``actual_zero`` is set (e.g. a liability that never happened). A line whose estimate is 0 needs no flag.
- Rate (``cmc_utils``, exact Fisher): same basis -> discount_rate; real flows + nominal rate -> real_rate; nominal
  flows + real rate -> nominal_rate; ``effective_rate`` percent (16, 6). Discount factor 1/(1+r)^year (year 0 = 1).
- Results ``year_result_ids`` -> ``cmc.lcc.year.result`` (one per year 0..horizon-1): ``year``,
  ``calendar_year`` = start_year + year, ``volume``, ``revenue`` = volume x price, ``cost``, ``recurring_cost``,
  ``non_recurring_cost``, ``net_flow`` = revenue - cost, ``discount_factor`` (16, 6), ``pv_cost``, ``pv_net``,
  ``cumulative_net``, ``cumulative_pv``, ``committed_cost`` (lines with that commit_year),
  ``cumulative_committed_pct`` / ``cumulative_incurred_pct`` (of lc_cost, (16, 2)), ``is_actual``,
  ``incremental_flow`` = net_flow - baseline net_flow of the same CALENDAR year (RN-001: rows are matched on
  ``start_year + year``, never on the index; a calendar year without a baseline row counts 0; 0 without baseline).
  ``phase_result_ids`` -> ``cmc.lcc.phase.result`` (cost view, one per phase x category present): ``phase``,
  ``category``, ``amount``, ``pv_amount``, ``share_pct`` (16, 2). Conservation (INT-05): sum phase amounts =
  sum year costs = lc_cost.
- Header outputs: ``effective_rate``, ``lc_cost``, ``lc_cost_npv``, ``recurring_cost``, ``non_recurring_cost``,
  ``lifetime_volume``, ``lc_unit_cost`` = lc_cost / lifetime_volume (16, 4; 0 without volume), ``lc_revenue``,
  ``lc_profit`` = revenue - cost, ``lc_profit_npv`` = NPV of the net flows, ``has_breakeven`` /
  ``breakeven_year`` (calendar year of the first cumulative_net >= 0 after a negative start), ``has_actuals``,
  ``absorbed_to_date``, ``residual_cost``, ``residual_volume``, ``residual_unit_cost`` (16, 4),
  ``incremental_npv`` (RN-001) = NPV, at the model's effective rate, of the incremental flows on the COMMON
  calendar timeline: from min(start_year, baseline.start_year) to the last calendar year of either model, the
  flow of each calendar year = model net_flow - baseline net_flow (a year outside a model's horizon counts 0 for
  it), discounted 1/(1+r)^(calendar_year - min start year). Years present only in the baseline enter the NPV but
  get no year_result row (rows stay one per own horizon year). Same start year and horizon: = lc_profit_npv -
  baseline.lc_profit_npv. Hand case: baseline 2026 flows -5,000 / 10,000 / 10,000, alternative 2027 flows
  10,000 / 10,000 at 10%: incremental 2026 +5,000, 2027 0, 2028 0 -> incremental_npv 5,000.
- Residual (Turney Ch8/14): rate in force of a model = its residual_unit_cost. Without actual years: absorbed 0,
  residual = lc_cost / lifetime volume. With actual years: absorbed_to_date = previous.absorbed_to_date + (volume
  of the actual years not actual in previous) x previous rate in force (without previous: x own lc_unit_cost);
  residual_cost = lc_cost - absorbed; residual_volume = volume of the non-actual years; residual_unit_cost =
  residual_cost / residual_volume. RN-001: "actual in previous" is matched on the CALENDAR year (start_year +
  year of each model), never on the index; lc_cost of a revision is the whole life cost of the account (a
  revision starting later carries the cost to date as a line of its year 0). Hand case: a (2026, previous =
  the 2026 $2 estimate) actual 2026/2027 -> absorbed 6,000, residual 2.80; revision c starts 2028, previous a,
  2028 actual 2,500 units -> absorbed 6,000 + 2,500 x 2.80 = 13,000, residual (20,000 - 13,000) / 2,500 = 2.80.

Fixture (company A = env company, rates published by a computed producer closing run, Jan 2026)
  Output measure "units" (statistical). Product cost objects: P 180 (Jan) / 170 (Feb); K 50 (Jan) / 48.5 (Feb)
  / 46.5 (Mar); F 400 (Jan); component C2 80 (Jan). Centers: assembly (parts 2.0, insertions 0.10), setup
  (setups 150), machining (minutes 1.5), service (service hours 100). A plan-version rate of P 1.0 (never read).
"""

from datetime import date

from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
D_JAN = date(2026, 1, 15)


class TlcCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        Driver = cls.env["cmc.cost.driver"]
        cls.units = Driver.create({"name": "WP16 units", "code": "WP16UN", "company_id": False, "measure_kind": "statistical"})
        cls.parts = Driver.create({"name": "WP16 parts", "code": "WP16PA", "company_id": False})
        cls.insertions = Driver.create({"name": "WP16 insertions", "code": "WP16IN", "company_id": False})
        cls.setups = Driver.create({"name": "WP16 setups", "code": "WP16SU", "company_id": False})
        cls.minutes = Driver.create({"name": "WP16 machine minutes", "code": "WP16MM", "company_id": False})
        cls.service_hours = Driver.create({"name": "WP16 service hours", "code": "WP16SH", "company_id": False})
        cls.assembly = cls._center("WP16 assembly")
        cls.setup = cls._center("WP16 setup")
        cls.machining = cls._center("WP16 machining")
        cls.service = cls._center("WP16 service")
        Product = cls.env["product.product"]
        cls.p = Product.create({"name": "WP16 P"})
        cls.k = Product.create({"name": "WP16 K"})
        cls.f = Product.create({"name": "WP16 F"})
        cls.c1_product = Product.create({"name": "WP16 C1"})
        cls.c2_product = Product.create({"name": "WP16 C2"})
        cls.obj_p, cls.obj_k, cls.obj_f, cls.obj_c2 = cls.env["cmc.cost.object"]._cmc_ensure_objects(
            cls.p | cls.k | cls.f | cls.c2_product
        )
        cls.supplier = cls.env["res.partner"].create({"name": "WP16 supplier"})
        cls.customer = cls.env["res.partner"].create({"name": "WP16 customer"})
        cls.env["account.analytic.line"].create(
            {"name": "WP16 cost", "account_id": cls.machining.id, "amount": -900, "date": D_JAN}
        )
        cls.producer = cls.env["cmc.closing.run"].create(
            {"name": "WP16 producer", "date_from": JAN, "date_to": date(2026, 1, 31), "method": "direct"}
        )
        cls.producer.action_compute()
        cls._publish(cls.producer, [
            (cls.obj_p, cls.units, JAN, 180.0), (cls.obj_p, cls.units, FEB, 170.0),
            (cls.obj_k, cls.units, JAN, 50.0), (cls.obj_k, cls.units, FEB, 48.5), (cls.obj_k, cls.units, MAR, 46.5),
            (cls.obj_f, cls.units, JAN, 400.0), (cls.obj_c2, cls.units, JAN, 80.0),
            (cls.assembly, cls.parts, JAN, 2.0), (cls.assembly, cls.insertions, JAN, 0.10),
            (cls.setup, cls.setups, JAN, 150.0), (cls.machining, cls.minutes, JAN, 1.5),
            (cls.service, cls.service_hours, JAN, 100.0),
        ])
        # a plan (version) rate is a plan, never the current actual cost: must not be read
        cls.version = cls.env["cmc.plan.version"].create(
            {"name": "WP16 plan", "date_from": JAN, "date_to": date(2026, 12, 31), "version_type": "standard"}
        )
        cls.env["cmc.rate"].create({
            "holder_model": cls.obj_p._name, "holder_id": cls.obj_p.id, "driver_id": cls.units.id, "date": JAN,
            "value": 1.0, "origin_model": cls.version._name, "origin_id": cls.version.id,
        })
        cls.user = new_test_user(cls.env, login="cmc_wp16_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_wp16_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(cls.env, login="cmc_wp16_outsider", groups="base.group_user")

    @classmethod
    def _center(cls, name):
        return cls.env["account.analytic.account"].create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final"})

    @classmethod
    def _publish(cls, producer, rows):
        cls.env["cmc.rate"].with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(
            producer, [{"holder": h, "driver": d, "date": m, "value": v} for h, d, m, v in rows]
        )

    # ---- TGT-01 -------------------------------------------------------

    def _target(self, user=None, product=None, **kw):
        vals = {"name": "WP16 target", "product_id": (product or self.p).id, "unit_driver_id": self.units.id,
                "date": D_JAN, "target_price": 200.0, "target_margin_pct": 25.0}
        vals.update(kw)
        model = self.env["cmc.target.cost"]
        return (model.with_user(user) if user else model).create(vals)

    def _deploy(self, target, c2_source="manual"):
        """Hand case: F1 60% / F2 40%; C1 100 (purchased) = 80% F1 + 20% F2; C2 80 = 25% F1 + 75% F2."""
        Function = self.env["cmc.target.cost.function"].with_env(target.env)
        Component = self.env["cmc.target.cost.component"].with_env(target.env)
        f1 = Function.create({"target_id": target.id, "name": "F1 performance", "importance_pct": 60.0})
        f2 = Function.create({"target_id": target.id, "name": "F2 look", "importance_pct": 40.0})
        c1 = Component.create({"target_id": target.id, "name": "C1 motor", "product_id": self.c1_product.id,
                               "current_cost": 100.0, "purchased": True, "partner_id": self.supplier.id})
        c2 = Component.create({"target_id": target.id, "name": "C2 housing", "product_id": self.c2_product.id,
                               "cost_source": c2_source, "current_cost": 80.0 if c2_source == "manual" else 0.0})
        self.env["cmc.target.cost.matrix"].with_env(target.env).create([
            {"target_id": target.id, "component_id": c1.id, "function_id": f1.id, "share_pct": 80.0},
            {"target_id": target.id, "component_id": c1.id, "function_id": f2.id, "share_pct": 20.0},
            {"target_id": target.id, "component_id": c2.id, "function_id": f1.id, "share_pct": 25.0},
            {"target_id": target.id, "component_id": c2.id, "function_id": f2.id, "share_pct": 75.0},
        ])
        return f1, f2, c1, c2

    # ---- TGT-02 -------------------------------------------------------

    def _feature(self, alternatives, user=None, **kw):
        """alternatives = {name: [(holder, driver, quantity, guideline or None), ...]}"""
        model = self.env["cmc.feature.costing"]
        costing = (model.with_user(user) if user else model).create({"name": "WP16 features", "date": D_JAN, **kw})
        Alt = self.env["cmc.feature.alternative"].with_env(costing.env)
        Var = self.env["cmc.feature.variable"].with_env(costing.env)
        alts = {}
        for name, variables in alternatives.items():
            alts[name] = Alt.create({"costing_id": costing.id, "name": name})
            Var.create([
                {"costing_id": costing.id, "alternative_id": alts[name].id, "name": "%s %s" % (driver.name, qty),
                 "holder_model": holder._name, "holder_id": holder.id, "driver_id": driver.id, "quantity": qty,
                 "guideline_id": guideline.id if guideline else False}
                for holder, driver, qty, guideline in variables
            ])
        return costing, alts

    # ---- LCC ----------------------------------------------------------

    def _lcc(self, years, costs, user=None, **kw):
        """years = [(year, volume, price, is_actual)], costs = [dict(cost line vals)]"""
        vals = {"name": "WP16 LCC", "product_id": self.p.id, "start_year": 2026, "horizon_years": 4,
                "discount_rate": 0.0, "rate_basis": "real", "flows_basis": "real",
                "year_ids": [(0, 0, {"year": y, "volume": v, "price_unit": p, "is_actual": a}) for y, v, p, a in years],
                "cost_line_ids": [(0, 0, c) for c in costs]}
        vals.update(kw)
        model = self.env["cmc.lcc.model"]
        return (model.with_user(user) if user else model).create(vals)

    @staticmethod
    def _cost(name, phase, category, year, amount, **kw):
        return {"name": name, "phase": phase, "category": category, "year": year, "amount": amount, **kw}

    def _base_lcc(self, **kw):
        """Hand case (4 years, 10,000 units at 5.0): costs 20,000 / 10,500 / 11,000 / 8,000 = 49,500."""
        years = [(0, 0, 0.0, False), (1, 4000, 5.0, False), (2, 4000, 5.0, False), (3, 2000, 5.0, False)]
        costs = [
            self._cost("R&D", "rd", "usual", 0, 20000.0, recurrence="non_recurring"),
            self._cost("Production y1", "production", "usual", 1, 8000.0),
            self._cost("Production y2", "production", "usual", 2, 8000.0),
            self._cost("Production y3", "production", "usual", 3, 4000.0),
            self._cost("Distribution y1", "distribution", "usual", 1, 2000.0),
            self._cost("Distribution y2", "distribution", "usual", 2, 2000.0),
            self._cost("Distribution y3", "distribution", "usual", 3, 1000.0),
            self._cost("Image loss y1", "distribution", "less_tangible", 1, 500.0),
            self._cost("Warranty y2", "use", "hidden", 2, 1000.0, commit_year=1),
            self._cost("Warranty y3", "use", "hidden", 3, 1000.0, commit_year=1),
            # contingent take-back/disposal liability: 20% x 10,000 = EMV 2,000
            self._cost("Disposal liability", "take_back", "liability", 3, 10000.0, probability_pct=20.0),
        ]
        return self._lcc(years, costs, **kw)
