"""WP-16 TGT-02 - feature costing: design variables x driver rates from the registry per design alternative,
against the allowable cost, with design cost guidelines (limits and relative factors). CONTRACT: see common.py."""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .common import TlcCase


@tagged("post_install", "-at_install")
class TestFeatureCosting(TlcCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Guideline = cls.env["cmc.design.guideline"]
        cls.g_parts = Guideline.create({"name": "WP16 max 15 parts", "driver_id": cls.parts.id, "max_quantity": 15.0})
        cls.g_setups = Guideline.create({"name": "WP16 single setup", "driver_id": cls.setups.id, "max_quantity": 1.0})
        cls.g_smt = Guideline.create({"name": "WP16 SMT insertion", "driver_id": cls.insertions.id, "relative_factor": 1.0})
        cls.g_robot = Guideline.create({"name": "WP16 robot insertion", "driver_id": cls.insertions.id, "relative_factor": 3.0})
        cls.g_hand = Guideline.create({"name": "WP16 hand insertion", "driver_id": cls.insertions.id, "relative_factor": 6.0})

    def _alternatives(self, **kw):
        # A: 20 parts x 2 + 2 setups x 150 + 60 min x 1.5 = 430; B: 24 + 150 + 150 = 324
        return self._feature({
            "A": [(self.assembly, self.parts, 20, None), (self.setup, self.setups, 2, None), (self.machining, self.minutes, 60, None)],
            "B": [(self.assembly, self.parts, 12, None), (self.setup, self.setups, 1, None), (self.machining, self.minutes, 100, None)],
        }, **kw)

    def _target_f(self):
        # allowable of F = 500 x (1 - 30%) = 350
        target = self._target(product=self.f, target_price=500.0, target_margin_pct=30.0)
        target.action_compute()
        return target

    def test_tgt02_estimated_cost_per_alternative(self):
        # TGT-02: cost = sum of design variable x driver rate; B is the cheapest
        costing, alts = self._alternatives()
        costing.action_compute()
        rows = costing.result_ids.sorted("rank")
        self.assertRecordValues(rows, [
            {"alternative_id": alts["B"].id, "estimated_cost": 324.0, "rank": 1, "meets_target": True},
            {"alternative_id": alts["A"].id, "estimated_cost": 430.0, "rank": 2, "meets_target": True},
        ])
        self.assertEqual(costing.best_alternative_id, alts["B"])

    def test_tgt02_variable_snapshot(self):
        # AD-113: the rate and the amount of each design variable are snapshotted at Compute
        costing, alts = self._alternatives()
        costing.action_compute()
        minutes_b = costing.variable_ids.filtered(lambda v: v.alternative_id == alts["B"] and v.driver_id == self.minutes)
        self.assertRecordValues(minutes_b, [{"rate_value": 1.5, "amount": 150.0}])

    def test_tgt02_gap_against_allowable_cost(self):
        # TGT-01 x TGT-02: iterate until the target is met - A is 80 over the 350 allowable, B 26 under
        costing, alts = self._alternatives(target_cost_id=self._target_f().id)
        costing.action_compute()
        rows = costing.result_ids.sorted(lambda r: r.alternative_id.name)
        self.assertRecordValues(rows, [
            {"alternative_id": alts["A"].id, "allowable_cost": 350.0, "gap": 80.0, "meets_target": False},
            {"alternative_id": alts["B"].id, "allowable_cost": 350.0, "gap": -26.0, "meets_target": True},
        ])

    def test_tgt02_draft_target_cannot_be_the_bar(self):
        # AD-002: a draft target sheet is no certified allowable cost
        target = self._target(product=self.f, target_price=500.0, target_margin_pct=30.0)
        costing, _alts = self._alternatives(target_cost_id=target.id)
        with self.assertRaises(UserError):
            costing.action_compute()

    def test_tgt02_guideline_violations(self):
        # TGT-02: A breaks "max 15 parts" (20, excess 5) and "single setup" (2, excess 1); B breaks none
        costing, alts = self._alternatives()
        costing.action_compute()
        violations = costing.violation_ids.filtered(lambda v: v.alternative_id == alts["A"]).sorted(lambda v: v.guideline_id.id)
        self.assertRecordValues(violations, [
            {"guideline_id": self.g_parts.id, "quantity": 20.0, "max_quantity": 15.0, "excess": 5.0},
            {"guideline_id": self.g_setups.id, "quantity": 2.0, "max_quantity": 1.0, "excess": 1.0},
        ])
        self.assertFalse(costing.violation_ids.filtered(lambda v: v.alternative_id == alts["B"]))
        self.assertEqual(costing.result_ids.filtered(lambda r: r.alternative_id == alts["A"]).violation_count, 2)

    def test_tgt02_relative_cost_guidelines(self):
        # Turney Ch8: 50 insertions at 0.10 - SMT 1.0x = 5, robot 3x = 15, hand 6x = 30
        costing, alts = self._feature({
            "SMT": [(self.assembly, self.insertions, 50, self.g_smt)],
            "Robot": [(self.assembly, self.insertions, 50, self.g_robot)],
            "Hand": [(self.assembly, self.insertions, 50, self.g_hand)],
        })
        costing.action_compute()
        cost = {r.alternative_id.name: r.estimated_cost for r in costing.result_ids}
        self.assertEqual(cost, {"SMT": 5.0, "Robot": 15.0, "Hand": 30.0})
        hand = costing.variable_ids.filtered(lambda v: v.alternative_id == alts["Hand"])
        self.assertAlmostEqual(hand.rate_value, 0.6, places=6)

    def test_tgt02_missing_rate_fails_loud(self):
        # AD-108: a design variable without a published rate is not costed at zero
        costing, _alts = self._feature({"X": [(self.service, self.parts, 3, None)]})
        with self.assertRaises(UserError):
            costing.action_compute()

    def test_tgt02_rates_producer_locked(self):
        # AD-002: the producer of the rates cannot be reset while the feature costing is computed
        costing, _alts = self._alternatives()
        costing.action_compute()
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()

    def test_tgt02_guidelines_are_manager_configuration(self):
        # AD-115: a CMC user reads the design guidelines, the manager maintains them
        self.assertTrue(self.env["cmc.design.guideline"].with_user(self.user).search([("id", "=", self.g_parts.id)]))
        with self.assertRaises(AccessError):
            self.env["cmc.design.guideline"].with_user(self.user).create({"name": "x", "driver_id": self.parts.id})
        guideline = self.env["cmc.design.guideline"].with_user(self.manager).create({"name": "WP16 m", "driver_id": self.parts.id})
        self.assertTrue(guideline)
