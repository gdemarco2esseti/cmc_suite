from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCvrActualDerivation(TransactionCase):
    """AC3.x — in actual mode the fixed cost is derived from the center's
    analytic lines on plain ORM create/write (import, RPC), not only by the
    onchange or action_recompute(): a CF=0 left by an import was presented as
    a break-even quantity of 0."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "CVR derive C", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.center_b = cls.env["account.analytic.account"].create(
            {"name": "CVR derive B", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        # AD-005: cost = NEGATIVE amount (same convention as test_cvr.py)
        line = cls.env["account.analytic.line"]
        line.create({"name": "fx C jan", "account_id": cls.center.id, "amount": -1000,
                     "date": date(2026, 1, 15), "cmc_behavior": "fixed"})
        line.create({"name": "var C jan", "account_id": cls.center.id, "amount": -400,
                     "date": date(2026, 1, 15), "cmc_behavior": "variable"})
        line.create({"name": "fx C feb", "account_id": cls.center.id, "amount": -2500,
                     "date": date(2026, 2, 15), "cmc_behavior": "fixed"})
        line.create({"name": "fx B jan", "account_id": cls.center_b.id, "amount": -3000,
                     "date": date(2026, 1, 15), "cmc_behavior": "fixed"})

    def _vals(self, **kw):
        vals = {"name": "derive", "mode": "consuntivo", "center_id": self.center.id,
                "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31),
                "price_unit": 10, "variable_unit": 4}
        vals.update(kw)
        return vals

    def _create(self, **kw):
        return self.env["cmc.cvr.analysis"].create(self._vals(**kw))

    # AC3.1 — create in consuntivo without fixed_cost derives CF from the center
    def test_ac31_create_actual_derives_fixed_cost(self):
        a = self._create()
        self.assertEqual(a.fixed_cost, 1000.0)
        self.assertAlmostEqual(a.bep_qty, 1000.0 / 6.0, places=2)

    # AC3.1 — batch create (import): every record derives its own CF
    def test_ac31_create_multi_derives_per_record(self):
        recs = self.env["cmc.cvr.analysis"].create([
            self._vals(name="a"),
            self._vals(name="b", center_id=self.center_b.id),
        ])
        self.assertEqual(recs.mapped("fixed_cost"), [1000.0, 3000.0])

    # AC3.1 — default mode is consuntivo: omitting mode must derive too
    def test_ac31_create_default_mode_derives(self):
        vals = self._vals()
        del vals["mode"]
        a = self.env["cmc.cvr.analysis"].create(vals)
        self.assertEqual(a.mode, "consuntivo")
        self.assertEqual(a.fixed_cost, 1000.0)

    # AC3.2 — switching a simulation to actual replaces the typed CF
    def test_ac32_write_mode_actual_replaces_simulated_cf(self):
        a = self._create(mode="simulazione", fixed_cost=500)
        self.assertEqual(a.fixed_cost, 500.0)
        a.write({"mode": "consuntivo"})
        self.assertEqual(a.fixed_cost, 1000.0)
        self.assertAlmostEqual(a.bep_qty, 1000.0 / 6.0, places=2)

    # AC3.3 — explicit CF on create in actual mode is kept (historical import)
    def test_ac33_create_explicit_fixed_cost_kept(self):
        a = self._create(fixed_cost=777)
        self.assertEqual(a.fixed_cost, 777.0)

    # AC3.3 — explicit CF on write in actual mode is kept
    def test_ac33_write_explicit_fixed_cost_kept(self):
        a = self._create(fixed_cost=777)
        a.write({"fixed_cost": 888})
        self.assertEqual(a.fixed_cost, 888.0)

    # AC3.3 — mode switch + explicit CF in the same write: explicit wins
    def test_ac33_write_mode_with_explicit_fixed_cost_kept(self):
        a = self._create(mode="simulazione", fixed_cost=500)
        a.write({"mode": "consuntivo", "fixed_cost": 650})
        self.assertEqual(a.fixed_cost, 650.0)

    # simulation is never overwritten by derivation (regression guard)
    def test_simulation_create_without_cf_not_derived(self):
        a = self._create(mode="simulazione")
        self.assertEqual(a.fixed_cost, 0.0)
        a.write({"date_to": date(2026, 2, 28)})
        self.assertEqual(a.fixed_cost, 0.0)

    # AC3.4 — changing the center in actual mode re-derives CF
    def test_ac34_write_center_rederives(self):
        a = self._create(fixed_cost=1000)
        a.write({"center_id": self.center_b.id})
        self.assertEqual(a.fixed_cost, 3000.0)

    # AC3.4 — widening the period in actual mode re-derives CF
    def test_ac34_write_dates_rederives(self):
        a = self._create(fixed_cost=1000)
        a.write({"date_to": date(2026, 2, 28)})
        self.assertEqual(a.fixed_cost, 3500.0)
        a.write({"date_from": date(2026, 2, 1)})
        self.assertEqual(a.fixed_cost, 2500.0)

    # AC3.5 — the curve after create is built on the derived CF, not on CF=0
    def test_ac35_curve_consistent_with_derived_cf(self):
        a = self._create()
        self.assertEqual(len(a.curve_ids), 4)
        origin = a.curve_ids.filtered(lambda c: c.volume == 0.0)
        self.assertEqual(origin.total_cost, 1000.0)
        at_bep = a.curve_ids.sorted("volume")[1]
        self.assertAlmostEqual(at_bep.volume, a.bep_qty, places=2)
        self.assertAlmostEqual(at_bep.revenue, at_bep.total_cost, places=2)

    # AC3.5 — the curve after a center change follows the re-derived CF
    def test_ac35_curve_follows_rederived_cf(self):
        a = self._create(fixed_cost=1000)
        a.write({"center_id": self.center_b.id})
        origin = a.curve_ids.filtered(lambda c: c.volume == 0.0)
        self.assertEqual(origin.total_cost, 3000.0)
