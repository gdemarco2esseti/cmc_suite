"""WP-12 - ronin objections RN-001 / RN-002 / RN-003 (regressions on the ABB engine and its target version).

CONTRACT (see common.py for the whole WP)
- RN-001: a committed_fixed resource with ZERO demand (MACH overridden to 0: MACHR is used only by MACH) still
  computes: supply 2,000 h, budgeted 30,000, rate = budgeted_cost / max(supply_qty, need) = 15 (the guard
  rate 0 applies only when that divisor is 0), assigned 0, unused 2,000 h / 30,000, ONE idle capacity state
  2,000 h / 30,000 owned by ``idle_responsibility_id`` (SALES). Conservation still holds.
  Figures: TECH 140 + 220 = 360 h -> 1 FTE, 48,000, rate 30, idle 1,240 h / 37,200; TRUCK as main fixture
  (50,000, assigned 27,500, idle 22,500); ENERGY 0. Total 128,000 = assigned 38,300 + unused 89,700, all
  unused owned by SALES.
- RN-002: at most ONE non-draft ``cmc.abb.run`` per ``target_version_id`` (Compute of a second run on the
  same target -> UserError; after a reset of the first it computes). Budget lines with ``abb_run_id`` of a
  computed run cannot be written nor unlinked by hand (UserError, users and managers alike): only the reset
  of their run removes them; hand-typed lines of the same version stay editable. Rates: ``cmc.rate`` gets
  ``abb_run_id`` (M2o cmc.abb.run, index, copy False) = the run that published the row; reset / unlink of a
  run deletes ONLY the rates with its ``abb_run_id``, never a hand-typed rate of the target version (even on
  the same holder x unit the run publishes for).
- RN-003: ``cmc.abb.variance.run`` dates on month boundaries (AD-105): ``date_from`` first day of a month,
  ``date_to`` last day of a month, else ValidationError.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import AbbCase


@tagged("post_install", "-at_install")
class TestAbbIntegrity(AbbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a = cls.company_a
        cls.user_a = new_test_user(
            cls.env, login="wp12_rn_user_a", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=a.id, company_ids=[(6, 0, a.ids)],
        )

    def _as(self, user):
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=self.company_a.ids))

    # ---- RN-001 ---------------------------------------------------------------

    def test_rn001_committed_fixed_without_demand_is_all_idle(self):
        # RN-001 / CAP-05: a machine nobody plans to use still costs its depreciation - all of it is idle, owned by sales
        run = self._abb_run(target_version_id=False, overrides=[{"activity_id": self.mach.id, "quantity": 0}])
        self.assertEqual(run.state, "done")
        self.assertRecordValues(self._res(run, self.machr), [{
            "demand_qty": 0.0, "supply_qty": 2000.0, "unused_qty": 2000.0, "budgeted_cost": 30000.0,
            "rate": 15.0, "assigned_cost": 0.0, "unused_cost": 30000.0,
        }])
        states = run.capacity_state_ids.filtered(lambda s: s.resource_id == self.machr and s.quantity)
        self.assertRecordValues(states, [{"state": "idle", "quantity": 2000.0, "cost": 30000.0, "responsibility_id": self.sales.id}])

    def test_rn001_zero_demand_run_conserves_cost(self):
        # RN-001: the idle machine's cost must not vanish from budgeted = assigned + unused
        run = self._abb_run(target_version_id=False, overrides=[{"activity_id": self.mach.id, "quantity": 0}])
        self.assertAlmostEqual(run.total_budgeted_cost, 128000.0, places=2)
        self.assertAlmostEqual(run.total_assigned_cost, 38300.0, places=2)
        self.assertAlmostEqual(run.total_unused_cost, 89700.0, places=2)
        self.assertAlmostEqual(sum(run.capacity_state_ids.mapped("cost")), 128000.0, places=2)
        self.assertEqual(run._cmc_unused_by_responsibility(), {self.sales.id: 89700.0})

    # ---- RN-002: one live run per target version ------------------------------

    def test_rn002_second_run_on_same_target_rejected(self):
        # RN-002: two computed runs on one budget version would double its lines and fight over its rates
        first = self._abb_run(env=self._as(self.user_a))
        second = self._abb_run(compute=False, env=self._as(self.user_a), name="WP12 ABB 2027 bis")
        with self.assertRaises(UserError):
            second.action_compute()
        self.assertEqual(second.state, "draft")
        self.assertEqual(len(self.bud27.budget_line_ids), 48)
        first.with_user(self.user_a).action_reset_to_draft()
        second.action_compute()
        self.assertEqual(second.state, "done")
        self.assertEqual(self.env["cmc.budget.line"].search([("version_id", "=", self.bud27.id)]).abb_run_id, second)

    def test_rn002_abb_budget_lines_not_editable_by_hand(self):
        # RN-002 / AD-113: the lines of a computed ABB are its output - only a reset of the run removes them
        run = self._abb_run()
        lines = self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)])
        for user in (self.user_a, self.manager_a):
            line = lines[0].with_user(user)
            with self.assertRaises(UserError):
                line.figure = 1.0
            with self.assertRaises(UserError):
                line.unlink()
        self.assertEqual(len(self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)])), 48)

    def test_rn002_hand_typed_budget_lines_stay_editable(self):
        # RN-002: the guard is on ABB output only - a planner's own line in the same version is still theirs
        self._abb_run()
        manual = self._as(self.user_a)["cmc.budget.line"].create({
            "version_id": self.bud27.id, "kind": "cost", "center_id": self.z.id, "element_id": self.sal.id,
            "date": date(2027, 1, 1), "figure": 100.0,
        })
        manual.figure = 200.0
        self.assertEqual(manual.figure, 200.0)
        manual.unlink()
        self.assertFalse(manual.exists())

    def test_rn002_reset_keeps_hand_typed_rates(self):
        # RN-002 / AD-108: reset withdraws what the run published, not what a planner typed on the version
        run = self._abb_run()
        published = self.env["cmc.rate"].search([("abb_run_id", "=", run.id)])
        self.assertTrue(published)
        self.assertEqual(published, self.env["cmc.rate"].search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.bud27.id)]))
        # same holder x unit the run publishes for (TECH x labour h), a component the run does not write
        hand = self._as(self.manager_a)["cmc.rate"].create({
            "holder_model": "cmc.abc.resource", "holder_id": self.tech.id, "driver_id": self.lhours.id, "component": "fixed",
            "date": date(2027, 1, 1), "value": 99.0, "origin_model": "cmc.plan.version", "origin_id": self.bud27.id,
            "company_id": self.company_a.id,
        })
        self.assertFalse(hand.abb_run_id)
        run.with_user(self.manager_a).action_reset_to_draft()
        self.assertFalse(published.exists())
        self.assertTrue(hand.exists())
        self.assertEqual(hand.value, 99.0)

    # ---- RN-003: variance on month boundaries ---------------------------------

    def _variance_vals(self, abb, date_from, date_to):
        return {"name": "WP12 RN-003", "company_id": self.company_a.id, "abb_run_id": abb.id, "date_from": date_from, "date_to": date_to}

    def test_rn003_variance_dates_on_month_boundaries(self):
        # RN-003 / AD-105: budget and ABB figures are monthly - a mid-month period would pro-rate them wrongly
        abb = self._abb_run(target_version_id=False)
        Variance = self._as(self.user_a)["cmc.abb.variance.run"]
        with self.assertRaises(ValidationError):
            Variance.create(self._variance_vals(abb, date(2027, 1, 15), date(2027, 6, 30)))
        with self.assertRaises(ValidationError):
            Variance.create(self._variance_vals(abb, date(2027, 1, 1), date(2027, 6, 15)))
        run = Variance.create(self._variance_vals(abb, date(2027, 1, 1), date(2027, 6, 30)))
        with self.assertRaises(ValidationError):
            run.date_to = date(2027, 2, 27)
        run.date_to = date(2027, 2, 28)
        self.assertEqual(run.date_to, date(2027, 2, 28))
