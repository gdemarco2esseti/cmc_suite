"""WP-10 DC-03 - product mix under ONE scarce resource, ranked by margin per unit of the resource.

CONTRACT (part of wp10_common; decision_type ``mix``; AD-118: multi-constraint LP is out of scope)
- Per mix line: mc_unit = price - variable; ``mc_per_resource`` = mc_unit / resource_per_unit;
  ``throughput_per_resource`` = (price - material) / resource_per_unit (TOC T/CU).
- Ranking by ``mix_ranking`` (contribution -> mc_per_resource desc, throughput -> throughput_per_resource
  desc; ties by line sequence/id); lines with a non-positive ranking key get rank but planned_qty 0.
- Greedy fill of ``resource_available``: planned_qty = min(max_demand, remaining / resource_per_unit)
  (fractional allowed, no rounding to units); resource_used = planned_qty x resource_per_unit.
- One ``cmc.decision.mix.result`` per mix line; header ``mix_total_mc`` = Σ planned_qty x mc_unit,
  ``mix_total_throughput`` = Σ planned_qty x (price - material), ``mix_result`` = mix_total_mc -
  ``fixed_cost``, ``resource_used`` = Σ; ``verdict`` False.
- ``resource_per_unit`` <= 0 -> ValidationError; ``resource_available`` <= 0 at Compute -> UserError.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .wp10_common import Wp10Case


@tagged("post_install", "-at_install")
class TestWp10Mix(Wp10Case):
    def _mix(self, **kw):
        vals = {
            "resource_available": 1000.0,
            "fixed_cost": 50000.0,
            "mix_line_ids": [
                (0, 0, {"name": "A", "price_unit": 500, "variable_unit": 300, "material_unit": 300,
                        "resource_per_unit": 2, "max_demand": 300}),
                (0, 0, {"name": "B", "price_unit": 600, "variable_unit": 350, "material_unit": 0,
                        "resource_per_unit": 5, "max_demand": 200}),
            ],
        }
        vals.update(kw)
        return self._decision("mix", **vals)

    def _results(self, decision):
        return {res.mix_line_id.name: res for res in decision.mix_result_ids}

    def test_rank_by_margin_per_scarce_hour(self):
        # DC-03 / Cinquini III.3: 200/2 = 100 per hour beats 250/5 = 50; A 300 (600 h), then B 400 h / 5 = 80
        decision = self._mix()
        decision.action_compute()
        res = self._results(decision)
        self.assertRecordValues(res["A"], [{"rank": 1, "mc_unit": 200.0, "mc_per_resource": 100.0, "planned_qty": 300.0,
                                            "resource_used": 600.0, "contribution": 60000.0}])
        self.assertRecordValues(res["B"], [{"rank": 2, "mc_unit": 250.0, "mc_per_resource": 50.0, "planned_qty": 80.0,
                                            "resource_used": 400.0, "contribution": 20000.0}])
        self.assertRecordValues(decision, [{"mix_total_mc": 80000.0, "mix_result": 30000.0, "resource_used": 1000.0}])

    def test_unit_margin_ranking_would_be_worse(self):
        # DC-03: the higher unit MC (B, 250) is NOT first: serving B first would give only 50,000 of MC
        decision = self._mix()
        decision.action_compute()
        self.assertGreater(decision.mix_total_mc, 200 * 250.0)

    def test_throughput_ranking_toc(self):
        # DC-03 / TOC: T/CU A (500-300)/2 = 100 < B 600/5 = 120 -> B takes all 1,000 h, throughput 120,000
        decision = self._mix(mix_ranking="throughput")
        decision.action_compute()
        res = self._results(decision)
        self.assertRecordValues(res["B"], [{"rank": 1, "throughput_per_resource": 120.0, "planned_qty": 200.0, "throughput": 120000.0}])
        self.assertRecordValues(res["A"], [{"rank": 2, "planned_qty": 0.0}])
        self.assertRecordValues(decision, [{"mix_total_throughput": 120000.0, "mix_total_mc": 50000.0}])

    def test_capacity_beyond_demand(self):
        # DC-03: spare resource stays unused once every demand is met
        decision = self._mix(resource_available=5000.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"resource_used": 1600.0, "mix_total_mc": 110000.0}])

    def test_negative_margin_is_not_produced(self):
        # DC-03: a product losing money per unit gets no capacity, whatever is left
        decision = self._mix(resource_available=5000.0, mix_line_ids=[
            (0, 0, {"name": "L", "price_unit": 100, "variable_unit": 120, "resource_per_unit": 1, "max_demand": 50}),
        ])
        decision.action_compute()
        self.assertEqual(self._results(decision)["L"].planned_qty, 0.0)

    def test_resource_per_unit_must_be_positive(self):
        # DC-03: a product not using the scarce resource has no ranking key
        with self.assertRaises(ValidationError):
            self._mix(mix_line_ids=[(0, 0, {"name": "Z", "price_unit": 10, "variable_unit": 5, "resource_per_unit": 0, "max_demand": 1})])

    def test_no_capacity_fails_loud(self):
        # DC-03: a mix without the scarce resource quantity is not computable
        decision = self._mix(resource_available=0.0)
        with self.assertRaises(UserError):
            decision.action_compute()

    def test_results_are_engine_rows(self):
        # DC-03 / AD-001: mix results are written by Compute only
        decision = self._mix()
        decision.action_compute()
        with self.assertRaises(UserError):
            decision.mix_result_ids[:1].planned_qty = 1.0
