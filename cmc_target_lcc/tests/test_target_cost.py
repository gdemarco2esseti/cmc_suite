"""WP-16 TGT-01 - target costing sheet: allowable cost, gap vs the current registry cost, deployment to functions
and components (function-cost matrix, value index), supplier target price. CONTRACT: see common.py."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import D_JAN, FEB, TlcCase


@tagged("post_install", "-at_install")
class TestTargetCost(TlcCase):
    def test_tgt01_allowable_cost_and_gap(self):
        # TGT-01: allowable = 200 x (1 - 25%) = 150; current = registry unit cost of P (180) -> gap 30 (16.67%)
        target = self._target()
        target.action_compute()
        self.assertRecordValues(target, [{
            "state": "done", "cost_object_id": self.obj_p.id, "allowable_cost": 150.0, "deployable_cost": 150.0,
            "current_cost": 180.0, "cost_gap": 30.0, "reduction_pct": 16.67, "target_met": False,
        }])

    def test_tgt01_target_after_distribution_cost(self):
        # TGT-01 / Turney Ch8: target cost = price - required profit - distribution (200 - 50 - 10 = 140)
        target = self._target(non_mfg_cost=10.0)
        target.action_compute()
        self.assertEqual(target.allowable_cost, 140.0)
        self.assertEqual(target.cost_gap, 40.0)

    def test_tgt01_drifting_current_cost_by_month(self):
        # TGT-01: the current cost is the one of the sheet month (Feb 170) -> the gap shrinks to 20
        target = self._target(date=FEB)
        target.action_compute()
        self.assertRecordValues(target, [{"current_cost": 170.0, "cost_gap": 20.0}])

    def test_tgt01_current_cost_from_any_producer_latest_wins(self):
        # AD-102/108: any producer run may publish the unit cost; the latest of the month is the current one
        other = self.env["cmc.closing.run"].create(
            {"name": "WP16 second producer", "date_from": self.producer.date_from, "date_to": self.producer.date_to, "method": "direct"}
        )
        other.action_compute()
        self._publish(other, [(self.obj_p, self.units, self.producer.date_from, 145.0)])
        target = self._target()
        target.action_compute()
        self.assertRecordValues(target, [{"current_cost": 145.0, "cost_gap": -5.0, "target_met": True}])

    def test_tgt01_plan_rate_is_never_the_current_cost(self):
        # AD-108: a plan-version rate (1.0) is a plan, not an actual: without a run rate Compute fails loud
        target = self._target(product=self.c1_product)
        with self.assertRaises(UserError):
            target.action_compute()
        target = self._target()
        target.action_compute()
        self.assertNotEqual(target.current_cost, 1.0)

    def test_tgt01_margin_out_of_range(self):
        # TGT-01: a margin of 100% or more leaves no cost to allow
        with self.assertRaises(ValidationError):
            self._target(target_margin_pct=100.0)
        with self.assertRaises(ValidationError):
            self._target(target_margin_pct=-1.0)

    def test_tgt01_function_deployment_and_value_index(self):
        # TGT-01 / Kaplan Ch11 App: F1 = 150 x 60% = 90, F2 = 60; cost F1 100 (55.56%), F2 80 (44.44%)
        target = self._target()
        f1, f2, _c1, _c2 = self._deploy(target)
        target.action_compute()
        rows = target.result_ids.filtered(lambda r: r.line_type == "function").sorted(lambda r: r.function_id.id)
        self.assertRecordValues(rows, [
            {"function_id": f1.id, "importance_pct": 60.0, "current_cost": 100.0, "cost_pct": 55.56,
             "target_cost": 90.0, "gap": 10.0, "value_index": 1.08},
            {"function_id": f2.id, "importance_pct": 40.0, "current_cost": 80.0, "cost_pct": 44.44,
             "target_cost": 60.0, "gap": 20.0, "value_index": 0.9},
        ])

    def test_tgt01_component_deployment_and_supplier_target_price(self):
        # TGT-01: C1 importance 0.6x80/100 + 0.4x20/80 = 58% -> 87; C2 42% -> 63; supplier target C1 = 87
        target = self._target()
        _f1, _f2, c1, c2 = self._deploy(target)
        target.action_compute()
        rows = target.result_ids.filtered(lambda r: r.line_type == "component").sorted(lambda r: r.component_id.id)
        self.assertRecordValues(rows, [
            {"component_id": c1.id, "partner_id": self.supplier.id, "importance_pct": 58.0, "current_cost": 100.0,
             "cost_pct": 55.56, "target_cost": 87.0, "gap": 13.0, "value_index": 1.044, "supplier_target_price": 87.0},
            {"component_id": c2.id, "partner_id": False, "importance_pct": 42.0, "current_cost": 80.0,
             "cost_pct": 44.44, "target_cost": 63.0, "gap": 17.0, "value_index": 0.945, "supplier_target_price": 0.0},
        ])

    def test_tgt01_cardinal_rule_component_targets_sum_to_deployable(self):
        # TGT-01 cardinal rule: sum of component targets = sum of function targets = allowable - assembly (150 - 7)
        target = self._target(assembly_cost=7.0)
        self._deploy(target)
        target.action_compute()
        self.assertEqual(target.deployable_cost, 143.0)
        for line_type in ("function", "component"):
            rows = target.result_ids.filtered(lambda r, t=line_type: r.line_type == t)
            self.assertAlmostEqual(sum(rows.mapped("target_cost")), 143.0, places=2)

    def test_tgt01_component_cost_from_registry(self):
        # TGT-01 / AD-108: a component priced from the registry (C2 unit cost 80) deploys as the typed one
        target = self._target()
        _f1, _f2, _c1, c2 = self._deploy(target, c2_source="registry")
        target.action_compute()
        row = target.result_ids.filtered(lambda r: r.component_id == c2)
        self.assertRecordValues(row, [{"current_cost": 80.0, "target_cost": 63.0}])

    def test_tgt01_importance_must_sum_to_100(self):
        # TGT-01: customer weights are a full 100% split
        target = self._target()
        f1, _f2, _c1, _c2 = self._deploy(target)
        f1.importance_pct = 50.0
        with self.assertRaises(UserError):
            target.action_compute()

    def test_tgt01_component_shares_must_sum_to_100(self):
        # TGT-01: each component's cost is split whole across the functions
        target = self._target()
        _f1, _f2, c1, _c2 = self._deploy(target)
        target.matrix_ids.filtered(lambda m: m.component_id == c1)[:1].share_pct = 70.0
        with self.assertRaises(UserError):
            target.action_compute()

    def test_tgt01_computed_sheet_is_locked_and_reset_clears(self):
        # AD-113: a computed sheet keeps its inputs; reset deletes the deployment and clears outputs
        target = self._target()
        self._deploy(target)
        target.action_compute()
        with self.assertRaises(UserError):
            target.target_price = 300.0
        with self.assertRaises(UserError):
            target.function_ids[:1].importance_pct = 10.0
        with self.assertRaises(UserError):
            target.allowable_cost = 1.0
        target.action_reset_to_draft()
        self.assertRecordValues(target, [{"state": "draft", "allowable_cost": 0.0, "current_cost": 0.0}])
        self.assertFalse(target.result_ids)

    def test_tgt01_producer_cannot_be_reset_while_read(self):
        # AD-002: the run that published the current cost stays computed while a sheet reads it
        target = self._target()
        target.action_compute()
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()
        target.action_reset_to_draft()
        self.producer.action_reset_to_draft()
        self.assertEqual(self.producer.state, "draft")
        self.assertEqual(target.date, D_JAN)
