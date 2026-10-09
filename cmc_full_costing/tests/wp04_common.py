"""WP-04 - shared fixture of the ``cmc_full_costing`` v2 red tests (FC-04, FC-05, FC-10, FC-11, CAP-03).

CONTRACT (whole WP-04; each test file repeats the part it covers)
- ``cmc.costing.run`` new INPUT fields (in ``_cmc_input_fields``, locked when computed):
  ``prime_cost_source`` Selection ``standard`` (default: product ``standard_price``, the v1 behaviour) /
  ``actual`` (FC-04: direct cost lines of the period stamped with the product cost object);
  ``produced_measure_id`` M2o ``cmc.cost.driver`` (FC-05: the measure whose ACTUAL facts give the
  produced quantity per product cost object); ``capacity_basis`` Selection ``actual`` (default: the whole
  center pool spread over the actual driver) / ``practical`` (CAP-03); ``markup_percent`` Float (FC-11).
- ``cmc.costing.run`` new OUTPUTS: ``unabsorbed_cost`` Monetary (cleared on reset);
  ``capacity_ids`` O2m ``cmc.costing.capacity`` (result rows, in ``_cmc_result_fields``):
  ``center_id``, ``fixed_cost``, ``variable_cost``, ``capacity_practical``, ``capacity_used``,
  ``fixed_rate``, ``fixed_charged``, ``unabsorbed_cost``.
- ``cmc.costing.result`` new fields: ``produced_qty``; ``unit_cost_primo`` / ``unit_cost_industriale`` /
  ``unit_cost_complessivo`` / ``unit_cost_economico_tecnico`` (level / produced qty, 0 without qty);
  ``cost_variable`` / ``cost_fixed`` (= ``cost_complessivo - cost_variable``) and their ``unit_*``;
  ``price_cost_plus`` (= ``unit_cost_complessivo * (1 + markup_percent / 100)``);
  ``cmc.costing.config.unit_amount`` (cumulative level / produced qty);
  methods ``_cmc_unit_cost_curve(volumes)`` and ``_cmc_bid_check(price)``.
- ``cmc.costing.run.action_cmc_prefill_lines()`` (draft only): driver lines from the ACTUAL facts of
  ALLOCATION measures in the run period/company, per final center x product cost object (FC-05).
- ``cmc.costing.quota`` new fields ``amount_fixed`` + ``amount_variable`` (= ``amount``).
- Compute records ``cmc.run.usage`` (consumer = costing run, origin = closing run) and publishes
  ``cmc.rate`` rows (holder = product cost object, driver = produced measure, month of the run) that the
  mixin reset/unlink removes.

Fixture (company A, March 2026, closing "direct", two final centers, no period pool, no figurative):
  Z: fixed indirect 1000, variable indirect 200; direct lines on Z: P1 MP 300 (raw material RM on the
     line), P1 MOD 120, P2 MP 150 (all variable, direct, stamped with the product cost object).
  W: fixed indirect 600.
  Driver lines (hours): Z P1 60 / P2 40 ; W P1 30 / P2 30.
  Produced facts (statistical measure): P1 60 (center Z) + 40 (center W) = 100 ; P2 50.
  Capacity analyses (March): Z practical 125 h (theoretical 150), W practical 60 h.
  standard_price: P1 50, P2 80, RM 7.
Company B: one final center ZB fixed 100, P1 MP 777 stamped with P1's company-B object, P1 produced 10.
"""

from datetime import date

from odoo.tests.common import TransactionCase

D_FROM = date(2026, 3, 1)
D_TO = date(2026, 3, 31)
D_MID = date(2026, 3, 15)


class Wp04Case(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")
        cls.company_a = cls.env["res.company"].create({"name": "CMC WP04 A"})
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP04 B"})
        cls.env_a = cls._company_env(cls.company_a)
        cls.env_b = cls._company_env(cls.company_b)
        Product = cls.env["product.product"]
        cls.p1 = Product.create({"name": "WP04 P1", "standard_price": 50})
        cls.p2 = Product.create({"name": "WP04 P2", "standard_price": 80})
        cls.rm = Product.create({"name": "WP04 raw material", "standard_price": 7})
        # ---- company A
        env = cls.env_a
        cls.hours = env["cmc.cost.driver"].create({"name": "WP04 hours", "code": "WP04H", "company_id": cls.company_a.id})
        cls.produced = cls._measure(env, cls.company_a, "WP04PQ")
        cls.z = cls._center(env, "WP04 Z", cls.company_a)
        cls.w = cls._center(env, "WP04 W", cls.company_a)
        cls.obj_p1, cls.obj_p2, cls.obj_rm = env["cmc.cost.object"]._cmc_ensure_objects(
            cls.p1 | cls.p2 | cls.rm, company=cls.company_a
        )
        cls._indirect(env, cls.z, 1000, "fixed")
        cls._indirect(env, cls.z, 200, "variable")
        cls._indirect(env, cls.w, 600, "fixed")
        cls._direct(env, cls.z, cls.obj_p1, 300, cls.mp, raw=cls.rm)
        cls._direct(env, cls.z, cls.obj_p1, 120, cls.mod)
        cls._direct(env, cls.z, cls.obj_p2, 150, cls.mp, raw=cls.rm)
        # outside the period (and outside the closing): never part of the run
        cls._direct(env, cls.z, cls.obj_p1, 999, cls.mp, day=date(2026, 4, 2))
        cls.closing = cls._closing(env, cls.company_a)
        cls.fact_p1_z = cls._fact(env, cls.produced, cls.obj_p1, 60, center=cls.z)
        cls._fact(env, cls.produced, cls.obj_p1, 40, center=cls.w)
        cls.fact_p2 = cls._fact(env, cls.produced, cls.obj_p2, 50, center=cls.z)
        # distractors: next month, another measure, a planned quantity
        cls._fact(env, cls.produced, cls.obj_p1, 500, day=date(2026, 4, 1))
        other = cls._measure(env, cls.company_a, "WP04OTHER")
        cls._fact(env, other, cls.obj_p1, 777)
        version = env["cmc.plan.version"].create(
            {"name": "WP04 budget", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "company_id": cls.company_a.id}
        )
        cls._fact(env, cls.produced, cls.obj_p1, 999, version=version)
        cls.cap_z = cls._capacity(env, cls.z, cls.company_a, theoretical=150, practical=125, actual=100)
        cls.cap_w = cls._capacity(env, cls.w, cls.company_a, theoretical=60, practical=60, actual=60)
        # ---- company B (same products, own cost objects)
        env = cls.env_b
        cls.hours_b = env["cmc.cost.driver"].create({"name": "WP04 hours B", "code": "WP04H", "company_id": cls.company_b.id})
        cls.produced_b = cls._measure(env, cls.company_b, "WP04PQ")
        cls.zb = cls._center(env, "WP04 ZB", cls.company_b)
        cls.obj_p1_b = env["cmc.cost.object"]._cmc_ensure_objects(cls.p1, company=cls.company_b)
        cls._indirect(env, cls.zb, 100, "fixed")
        cls._direct(env, cls.zb, cls.obj_p1_b, 777, cls.mp)
        cls.closing_b = cls._closing(env, cls.company_b)
        cls._fact(env, cls.produced_b, cls.obj_p1_b, 10, center=cls.zb)

    # ---- builders ---------------------------------------------------------

    @classmethod
    def _company_env(cls, company):
        return cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))

    @classmethod
    def _measure(cls, env, company, code):
        return env["cmc.cost.driver"].create(
            {"name": "Produced %s" % code, "code": code, "measure_kind": "statistical",
             "measure_type": "transaction", "company_id": company.id}
        )

    @classmethod
    def _center(cls, env, name, company):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": company.id}
        )

    @classmethod
    def _indirect(cls, env, center, cost, behavior, day=D_MID):
        return env["account.analytic.line"].create(
            {"name": "indirect", "account_id": center.id, "amount": -cost,  # AD-005: cost = negative amount
             "company_id": center.company_id.id, "cmc_behavior": behavior, "cmc_directness": "indirect", "date": day}
        )

    @classmethod
    def _direct(cls, env, center, cost_object, cost, element, raw=None, day=D_MID, behavior="variable"):
        """A direct MP/MOD line as cmc_mrp books it (element, variable, direct, finished-product cost object;
        on MP the line product is the RAW material, AD-106 inv. 4). Booked by hand: the bridge is not a
        dependency of cmc_full_costing and a stamped line is a stamped line whoever wrote it."""
        AL = env["account.analytic.line"]
        vals = {
            "name": "direct", "account_id": center.id, "amount": -cost, "company_id": center.company_id.id,
            "cmc_cost_element_id": element.id, "cmc_cost_object_id": cost_object.id,
            "cmc_behavior": behavior, "cmc_directness": "direct", "date": day,
        }
        if raw and "product_id" in AL._fields:
            vals["product_id"] = raw.id
        return AL.create(vals)

    @classmethod
    def _closing(cls, env, company):
        closing = env["cmc.closing.run"].create(
            {"name": "WP04 closing %s" % company.name, "method": "direct", "company_id": company.id,
             "date_from": D_FROM, "date_to": D_TO}
        )
        closing.action_compute()
        return closing

    @classmethod
    def _fact(cls, env, measure, cost_object, qty, center=None, day=D_MID, version=None):
        # manual facts are a supported source (feeder_key empty = entered by hand, AD-107); the MRP
        # feeder writes the very same rows
        return env["cmc.measure.quantity"].create(
            {"driver_id": measure.id, "date": day, "company_id": measure.company_id.id,
             "center_id": center.id if center else False, "cost_object_id": cost_object.id,
             "quantity": qty, "version_id": version.id if version else False}
        )

    @classmethod
    def _capacity(cls, env, center, company, theoretical, practical, actual):
        return env["cmc.capacity.analysis"].create(
            {"center_id": center.id, "company_id": company.id, "date_from": D_FROM, "date_to": D_TO,
             "capacity_theoretical": theoretical, "capacity_practical": practical, "capacity_actual": actual}
        )

    # ---- runs -------------------------------------------------------------

    def _lines_a(self):
        return [
            {"center_id": self.z.id, "product_id": self.p1.id, "driver_id": self.hours.id, "driver_qty": 60},
            {"center_id": self.z.id, "product_id": self.p2.id, "driver_id": self.hours.id, "driver_qty": 40},
            {"center_id": self.w.id, "product_id": self.p1.id, "driver_id": self.hours.id, "driver_qty": 30},
            {"center_id": self.w.id, "product_id": self.p2.id, "driver_id": self.hours.id, "driver_qty": 30},
        ]

    def _run_a(self, compute=True, env=None, **vals):
        env = env or self.env_a
        run = env["cmc.costing.run"].create({
            "name": "WP04 run A",
            "company_id": self.company_a.id,
            "closing_run_id": self.closing.id,
            "date_from": D_FROM,
            "date_to": D_TO,
            "base": "multiple",
            "product_ids": [(6, 0, (self.p1 | self.p2).ids)],
            "line_ids": [(0, 0, ln) for ln in self._lines_a()],
            "prime_cost_source": "actual",
            "produced_measure_id": self.produced.id,
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    def _run_b(self, compute=True):
        run = self.env_b["cmc.costing.run"].create({
            "name": "WP04 run B",
            "company_id": self.company_b.id,
            "closing_run_id": self.closing_b.id,
            "date_from": D_FROM,
            "date_to": D_TO,
            "base": "multiple",
            "product_ids": [(6, 0, self.p1.ids)],
            "line_ids": [(0, 0, {"center_id": self.zb.id, "product_id": self.p1.id,
                                 "driver_id": self.hours_b.id, "driver_qty": 10})],
            "prime_cost_source": "actual",
            "produced_measure_id": self.produced_b.id,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _res(run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)
