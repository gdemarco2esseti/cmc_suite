"""WP-11 FCS-02 / INT-04 - a scenario is a copy of a baseline version with changed inputs, compared side by side
with a delta P&L per line through the evaluator. CONTRACT: see common.py."""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import EVALUATOR_KEY, FIXED, PRICE, VARCOST, ScenarioCase


@tagged("post_install", "-at_install")
class TestComparison(ScenarioCase):
    def test_scenario_copy_is_a_draft_simulation_of_the_baseline(self):
        # FCS-02: copy a baseline (even approved) into a draft scenario carrying its inputs
        scenario = self._scenario()
        self.assertRecordValues(scenario, [{"state": "draft", "version_type": "simulation", "parent_id": self.baseline.id}])
        facts = self.env["cmc.measure.quantity"].search([("version_id", "=", scenario.id)])
        self.assertEqual(len(facts), 8)

    def test_create_scenario_action_opens_the_copy(self):
        # FCS-02: the button lands on the new scenario form
        action = self.baseline.action_cmc_create_scenario()
        self.assertEqual(action["res_model"], "cmc.plan.version")
        scenario = self.env["cmc.plan.version"].browse(action["res_id"])
        self.assertEqual(scenario.parent_id, self.baseline)
        self.assertEqual(scenario.version_type, "simulation")

    def test_assumption_register_travels_with_the_scenario(self):
        # INT-04: the copy carries the baseline assumptions; editing the scenario's leaves the baseline untouched
        self.env["cmc.plan.version.assumption"].create({"version_id": self.baseline.id, "name": "WP11 FX stable"})
        scenario = self._scenario()
        self.assertEqual(scenario.assumption_ids.mapped("name"), ["WP11 FX stable"])
        scenario.assumption_ids.name = "WP11 price +10%"
        self.assertEqual(self.baseline.assumption_ids.mapped("name"), ["WP11 FX stable"])

    def test_delta_pnl_per_line(self):
        # FCS-02: price A 10 -> 11: revenue A +1,000 (+10%), every other line delta 0; profit 1,300 -> 2,300
        scenario = self._scenario()
        self._set_input(scenario, self.center_a, PRICE, 11.0)
        comparison = self._comparison(scenario)
        comparison.action_compute()
        self.assertEqual(comparison.baseline_id, self.baseline)
        self.assertRecordValues(comparison, [{"state": "done", "baseline_profit": 1300.0, "scenario_profit": 2300.0, "delta_profit": 1000.0}])
        lines = self._by_key(comparison.line_ids)
        self.assertEqual(len(lines), 6)
        rev_a = lines[(self.center_a, self.el_revenue, self.baseline.date_from)]
        self.assertRecordValues(rev_a, [{"baseline_amount": 10000.0, "scenario_amount": 11000.0, "delta": 1000.0, "delta_pct": 10.0}])
        others = comparison.line_ids - rev_a
        self.assertEqual(set(others.mapped("delta")), {0.0})

    def test_cost_increase_is_a_negative_delta(self):
        # FCS-02 / AD-112: unit variable cost A 6 -> 6.6 is unfavourable: -600, -10%
        scenario = self._scenario()
        self._set_input(scenario, self.center_a, VARCOST, 6.6)
        comparison = self._comparison(scenario)
        comparison.action_compute()
        var_a = self._by_key(comparison.line_ids)[(self.center_a, self.el_variable, self.baseline.date_from)]
        self.assertRecordValues(var_a, [{"baseline_amount": -6000.0, "scenario_amount": -6600.0, "delta": -600.0, "delta_pct": -10.0}])
        self.assertEqual(comparison.delta_profit, -600.0)

    def test_line_only_in_scenario_counts_zero_on_baseline(self):
        # FCS-02: a new center in the scenario shows its full amount as delta; delta_pct 0 with no baseline
        scenario = self._scenario()
        center_c = self._center("WP11 center C")
        self._facts(scenario, {center_c: (5.0, 10.0, 1.0, 0.0)})
        comparison = self._comparison(scenario)
        comparison.action_compute()
        rev_c = self._by_key(comparison.line_ids)[(center_c, self.el_revenue, self.baseline.date_from)]
        self.assertRecordValues(rev_c, [{"baseline_amount": 0.0, "scenario_amount": 50.0, "delta": 50.0, "delta_pct": 0.0}])
        self.assertEqual(len(comparison.line_ids), 9)
        self.assertEqual(comparison.delta_profit, 40.0)

    def test_amounts_in_company_currency(self):
        # FCS-02 / INT-07: a scenario in another currency is compared at its fixed internal rate
        scenario = self._scenario()
        scenario.currency_rate = 2.0
        comparison = self._comparison(scenario)
        comparison.action_compute()
        fix_b = self._by_key(comparison.line_ids)[(self.center_b, self.el_fixed, self.baseline.date_from)]
        self.assertRecordValues(fix_b, [{"baseline_amount": -500.0, "scenario_amount": -1000.0, "delta": -500.0}])
        self.assertEqual(comparison.scenario_profit, 2600.0)

    def test_explicit_baseline_overrides_parent(self):
        # FCS-02: compare against another baseline than the one copied from
        other = self._version("WP11 other baseline", {self.center_a: (10.0, 1000.0, 6.0, 2000.0)})
        scenario = self._scenario()
        comparison = self._comparison(scenario, baseline_id=other.id)
        comparison.action_compute()
        self.assertRecordValues(comparison, [{"baseline_profit": 2000.0, "scenario_profit": 1300.0, "delta_profit": -700.0}])

    def test_no_baseline_raises(self):
        # FCS-02: a version not copied from anything needs an explicit baseline
        lone = self._version("WP11 lone", {self.center_a: (1.0, 1.0, 0.0, 0.0)})
        comparison = self._comparison(lone)
        self.assertFalse(comparison.baseline_id)
        with self.assertRaises(UserError):
            comparison.action_compute()

    def test_unknown_evaluator_raises(self):
        # AD-102: no module registered that evaluator -> loud failure, no empty comparison
        comparison = self._comparison(self._scenario(), evaluator_key="wp11_missing")
        with self.assertRaisesRegex(UserError, "wp11_missing"):
            comparison.action_compute()

    def test_reset_deletes_lines_and_clears_outputs(self):
        # AD-113: the comparison is a snapshot; reset drops it
        scenario = self._scenario()
        self._set_input(scenario, self.center_a, FIXED, 2500.0)
        comparison = self._comparison(scenario)
        comparison.action_compute()
        self.assertEqual(comparison.delta_profit, 500.0)
        comparison.action_reset_to_draft()
        self.assertRecordValues(comparison, [{"state": "draft", "baseline_profit": 0.0, "scenario_profit": 0.0, "delta_profit": 0.0}])
        self.assertFalse(comparison.line_ids)
        self.assertFalse(self.env["cmc.scenario.comparison.line"].search([("comparison_id", "=", comparison.id)]))

    def test_each_version_prepared_once(self):
        # AD-114: one batched read per version, never per line
        comparison = self._comparison(self._scenario())
        comparison.action_compute()
        self.assertEqual(sorted(self.prepare_calls), sorted([comparison.baseline_id.id, comparison.scenario_id.id]))
        self.assertEqual(comparison.evaluator_key, EVALUATOR_KEY)
