"""WP-07 / ABC-05 (batch drivers), ABC-08 (driver catalogue: type, weighted index), INT-02 (feeders).

CONTRACT (see abc2_common for the whole WP)
- ``cmc.abc.line.output_units`` + ``batch_size`` -> ``driver_qty`` = ceil(units / batch size) (computed,
  stored, editable): setups = units / batch size.
- ``cmc.cost.driver.cmc_component_ids`` (``cmc.abc.driver.component``: ``component_driver_id``, ``weight``):
  index qty per object = Σ weight x component fact qty (Kaplan Ch.6: simple 1 ... complex 10).
- Driver type (``measure_type`` transaction / duration / intensity) is snapshotted on
  ``cmc.abc.activity.cost.driver_type``; intensity quantities (direct-charge amounts) split pro rata.
- ``cmc.abc.run.action_cmc_prefill_lines()`` (draft only, UserError on a computed run; dates required):
  1. ``cmc.measure.quantity._cmc_refresh_feeders(date_from, date_to, company=run company)``;
  2. for each non-sustaining ``activity`` node of the run company with an allocation ``driver_id``,
     A->CO lines from the ACTUAL facts (no version) of the span per cost object (index drivers via their
     components), replacing the A->CO lines of those activities (idempotent).
  Compute reads the lines only: a later fact change / feeder refresh never changes a computed run (AD-113).
Hand numbers: orders C1 7 / C2 3 (April 50 and planned 99 ignored); complexity index C1 5x1 = 5,
C2 2x1 + 1x10 = 12; feeder "invoice lines per customer" C1 3 / C2 1.
"""

from datetime import date
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .abc2_common import D_FROM, D_MID, Abc2Case

FEED_KEY = "wp07_invoice_lines"


@tagged("post_install", "-at_install")
class TestAbc2Drivers(Abc2Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        cls.ord_m = cls._driver(env, "WP07ORDM", "transaction", level="customer")
        cls.simple = cls._driver(env, "WP07SIMPLE", "transaction", kind="statistical")
        cls.complex = cls._driver(env, "WP07COMPLEX", "transaction", kind="statistical")
        cls.cplx = cls._driver(env, "WP07CPLX", "transaction", level="customer")
        cls.cplx.cmc_component_ids = [
            (0, 0, {"component_driver_id": cls.simple.id, "weight": 1.0}),
            (0, 0, {"component_driver_id": cls.complex.id, "weight": 10.0}),
        ]
        cls.inv = cls._driver(env, "WP07INV", "transaction", level="customer")
        cls.inv.write({"source": "feeder", "feeder_key": FEED_KEY})
        cls.act_ord = cls._activity("Enter orders", "WP07AORD", cls.pr_sup, "customer", cls.ord_m)
        cls.act_cplx = cls._activity("Configure orders", "WP07ACPLX", cls.pr_sup, "customer", cls.cplx)
        cls.act_inv = cls._activity("Issue invoices", "WP07AINV", cls.pr_sup, "customer", cls.inv)
        cls._fact(env, cls.ord_m, cls.obj_c1, 7)
        cls._fact(env, cls.ord_m, cls.obj_c2, 3)
        cls._fact(env, cls.ord_m, cls.obj_c1, 50, day=date(2027, 4, 5))
        version = env["cmc.plan.version"].create(
            {"name": "WP07 plan", "date_from": D_FROM, "date_to": date(2027, 12, 31), "company_id": cls.company_a.id}
        )
        cls._fact(env, cls.ord_m, cls.obj_c1, 99, version=version)
        cls._fact(env, cls.simple, cls.obj_c1, 5)
        cls._fact(env, cls.simple, cls.obj_c2, 2)
        cls._fact(env, cls.complex, cls.obj_c2, 1)
        # source rows of the feeder: "invoice lines" per customer (a stand-in for the cmc_account feeder)
        stats = cls._center(env, "WP07 sales stats", cls.company_a)
        env["account.analytic.line"].create([
            {"name": "wp07inv", "account_id": stats.id, "company_id": cls.company_a.id, "date": D_MID,
             "partner_id": partner.id, "amount": 0.0}
            for partner in (cls.c1, cls.c1, cls.c1, cls.c2)
        ])
        cls.profile = {
            "key": FEED_KEY, "model": "account.analytic.line", "domain": [("name", "=like", "wp07inv%")],
            "date_field": "date", "quantity": "__count", "driver": cls.inv, "center_field": False,
            "object_field": "partner_id", "object_type": "customer",
        }

    def _prefill(self, run):
        Fact = type(self.env["cmc.measure.quantity"])
        with patch.object(Fact, "_cmc_feeder_profiles", lambda model: [dict(self.profile)]):
            run.action_cmc_prefill_lines()

    def _qty(self, run, activity):
        lines = run.line_ids.filtered(lambda ln: ln.activity_id == activity)
        return {ln.cost_object_id: ln.driver_qty for ln in lines}

    def test_int02_prefill_from_measure_facts(self):
        # INT-02: driver qty = actual facts of the span per object (April and the plan stay out)
        run = self._main_run(compute=False, edges=[])
        self._prefill(run)
        self.assertEqual(self._qty(run, self.act_ord), {self.obj_c1: 7.0, self.obj_c2: 3.0})
        self.assertEqual(run.line_ids.filtered(lambda ln: ln.activity_id == self.act_ord).driver_id, self.ord_m)

    def test_abc08_weighted_index_driver(self):
        # ABC-08: index = Σ weight x count (C1 5 simple; C2 2 simple + 1 complex x 10)
        run = self._main_run(compute=False, edges=[])
        self._prefill(run)
        self.assertEqual(self._qty(run, self.act_cplx), {self.obj_c1: 5.0, self.obj_c2: 12.0})

    def test_int02_prefill_refreshes_feeders(self):
        # INT-02: transactions -> driver mapping (invoice lines per customer) refreshed before the prefill
        run = self._main_run(compute=False, edges=[])
        self._prefill(run)
        self.assertEqual(self._qty(run, self.act_inv), {self.obj_c1: 3.0, self.obj_c2: 1.0})

    def test_int02_prefill_is_idempotent(self):
        # INT-02: prefilling twice replaces, never appends
        run = self._main_run(compute=False, edges=[])
        self._prefill(run)
        count = len(run.line_ids)
        self._prefill(run)
        self.assertEqual(len(run.line_ids), count)
        self.assertEqual(self._qty(run, self.act_ord), {self.obj_c1: 7.0, self.obj_c2: 3.0})

    def test_int02_prefill_refused_on_computed_run(self):
        # AD-113: a computed run keeps its inputs
        run = self._main_run()
        with self.assertRaises(UserError):
            self._prefill(run)

    def test_int02_computed_run_is_a_snapshot(self):
        # AD-107/113: a later fact change does not move a computed run (R3 4000 -> orders 7/3)
        run = self._main_run(compute=False, edges=[], capacity=[], trace_direct=False,
                             resource_lines=[{"resource_id": self.r3.id, "activity_id": self.act_ord.id, "percent": 100}])
        self._prefill(run)
        run.line_ids.filtered(lambda ln: ln.activity_id != self.act_ord).unlink()
        run.action_compute()
        self.assertAlmostEqual(self._res(run, self.obj_c1).amount, 2800.0, places=2)
        fact = self.env["cmc.measure.quantity"].search(
            [("driver_id", "=", self.ord_m.id), ("cost_object_id", "=", self.obj_c1.id), ("date", "=", D_FROM),
             ("version_id", "=", False)]
        )
        fact.quantity = 70
        run.invalidate_recordset()
        self.assertAlmostEqual(self._res(run, self.obj_c1).amount, 2800.0, places=2)
        self.assertEqual(self._qty(run, self.act_ord)[self.obj_c1], 7.0)

    def test_abc05_batch_driver_from_units(self):
        # ABC-05: setups = units / batch size (9000 / 100 = 90; 1050 / 100 -> 11 started batches)
        run = self._main_run(compute=False, edges=[])
        Line = self.env_a["cmc.abc.line"]
        big = Line.create({"run_id": run.id, "activity_id": self.setup.id, "cost_object_id": self.obj_p1.id,
                           "driver_id": self.setups.id, "output_units": 9000, "batch_size": 100})
        odd = Line.create({"run_id": run.id, "activity_id": self.setup.id, "cost_object_id": self.obj_p2.id,
                           "driver_id": self.setups.id, "output_units": 1050, "batch_size": 100})
        self.assertAlmostEqual(big.driver_qty, 90.0)
        self.assertAlmostEqual(odd.driver_qty, 11.0)

    def test_abc08_intensity_driver(self):
        # ABC-08 (Kaplan Ch.6): intensity = direct charge of the resources actually used (300 / 100)
        intensity = self._driver(self.env_a, "WP07ENG", "intensity")
        eng = self._activity("Engineer changes", "WP07ENGA", self.pr_mfg, False, intensity)
        run = self._main_run(
            trace_direct=False, output_measure_id=False, capacity=[],
            resource_lines=[{"resource_id": self.r3.id, "activity_id": eng.id, "percent": 100}],
            edges=[
                {"activity_id": eng.id, "cost_object_id": self.obj_p1.id, "driver_id": intensity.id, "driver_qty": 300},
                {"activity_id": eng.id, "cost_object_id": self.obj_p2.id, "driver_id": intensity.id, "driver_qty": 100},
            ],
        )
        self.assertEqual(self._act(run, eng).driver_type, "intensity")
        self.assertAlmostEqual(self._res(run, self.obj_p1).amount, 3000.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_p2).amount, 1000.0, places=2)
