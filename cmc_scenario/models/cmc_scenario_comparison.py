from odoo import api, fields, models
from odoo.exceptions import UserError


class CmcScenarioComparison(models.Model):
    _name = "cmc.scenario.comparison"
    _inherit = ["cmc.scenario.run.mixin"]
    _description = "Scenario Comparison"
    _order = "id desc"

    _cmc_input_fields = ("company_id", "evaluator_key", "scenario_id", "baseline_id")
    _cmc_result_fields = ("line_ids",)
    _cmc_output_fields = ("baseline_profit", "scenario_profit", "delta_profit")

    scenario_id = fields.Many2one("cmc.plan.version", required=True, check_company=True, index=True)
    baseline_id = fields.Many2one(
        "cmc.plan.version", compute="_compute_baseline_id", store=True, readonly=False, check_company=True,
        help="Defaults to the version the scenario was copied from.",
    )
    line_ids = fields.One2many("cmc.scenario.comparison.line", "comparison_id", copy=False)
    baseline_profit = fields.Monetary(readonly=True, copy=False)
    scenario_profit = fields.Monetary(readonly=True, copy=False)
    delta_profit = fields.Monetary(readonly=True, copy=False, help="Scenario - baseline: positive is favourable.")

    @api.depends("scenario_id.parent_id")
    def _compute_baseline_id(self):
        for run in self:
            run.baseline_id = run.scenario_id.parent_id

    def _cmc_compute(self):
        baseline = self.baseline_id
        if not baseline:
            raise UserError(self.env._("%s has no baseline: choose the version to compare it with.", self.scenario_id.display_name))
        base, scenario = self._scenario_amounts(baseline), self._scenario_amounts(self.scenario_id)
        currency = self.currency_id
        vals_list = []
        for key in self._scenario_sorted(base.keys() | scenario.keys()):
            before, after = base.get(key, 0.0), scenario.get(key, 0.0)
            delta = currency.round(after - before)
            vals_list.append({
                "comparison_id": self.id, "center_id": key[0], "element_id": key[1], "date": key[2],
                "baseline_amount": before, "scenario_amount": after, "delta": delta,
                "delta_pct": delta / abs(before) * 100 if before else 0.0,
            })
        # why sudo: users read the comparison, the engine writes it
        self.env["cmc.scenario.comparison.line"].sudo().create(vals_list)
        before, after = sum(base.values()), sum(scenario.values())
        self.write({"baseline_profit": before, "scenario_profit": after, "delta_profit": currency.round(after - before)})


class CmcScenarioComparisonLine(models.Model):
    _name = "cmc.scenario.comparison.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Scenario Comparison Line"
    _order = "date, center_id, element_id, id"

    comparison_id = fields.Many2one("cmc.scenario.comparison", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="comparison_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="comparison_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element")
    date = fields.Date(string="Month")
    baseline_amount = fields.Monetary()
    scenario_amount = fields.Monetary()
    delta = fields.Monetary(help="Scenario - baseline: positive is favourable.")
    delta_pct = fields.Float(string="Delta %", digits=(16, 2))
