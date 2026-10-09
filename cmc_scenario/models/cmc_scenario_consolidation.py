from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class CmcScenarioConsolidation(models.Model):
    _name = "cmc.scenario.consolidation"
    _inherit = ["cmc.scenario.run.mixin"]
    _description = "Version Consolidation"
    _order = "id desc"

    _cmc_input_fields = ("company_id", "evaluator_key", "version_id", "child_version_ids")
    _cmc_result_fields = ("line_ids",)
    _scenario_outputs = ("total_amount",)

    version_id = fields.Many2one("cmc.plan.version", string="Parent Version", required=True, check_company=True, index=True)
    child_version_ids = fields.Many2many(
        "cmc.plan.version", "cmc_scenario_consolidation_child_rel", "consolidation_id", "version_id",
        string="Child Versions", check_company=True,
    )
    line_ids = fields.One2many("cmc.scenario.consolidation.line", "consolidation_id", copy=False)
    total_amount = fields.Monetary(readonly=True, copy=False)

    @api.constrains("version_id", "child_version_ids")
    def _check_parent_not_child(self):
        if any(run.version_id in run.child_version_ids for run in self):
            raise ValidationError(self.env._("A version cannot be consolidated into itself."))

    def _cmc_compute(self):
        children = self.child_version_ids
        if not children:
            raise UserError(self.env._("Choose the child versions to consolidate."))
        draft = children.filtered(lambda child: child.state != "done")
        if draft:
            raise UserError(self.env._("%s is not approved: a consolidation reads approved versions only.", ", ".join(draft.mapped("display_name"))))
        totals = defaultdict(float)
        for child in children:
            for key, amount in self._scenario_amounts(child).items():
                totals[key] += amount
        currency = self.currency_id
        # why sudo: users read the consolidation, the engine writes it
        self.env["cmc.scenario.consolidation.line"].sudo().create([
            {"consolidation_id": self.id, "center_id": key[0], "element_id": key[1], "date": key[2], "amount": currency.round(totals[key])}
            for key in self._scenario_sorted(totals)
        ])
        self._cmc_record_usage(children)
        self.write({"total_amount": currency.round(sum(totals.values()))})


class CmcScenarioConsolidationLine(models.Model):
    _name = "cmc.scenario.consolidation.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Version Consolidation Line"
    _order = "date, center_id, element_id, id"

    consolidation_id = fields.Many2one("cmc.scenario.consolidation", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="consolidation_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="consolidation_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element")
    date = fields.Date(string="Month")
    amount = fields.Monetary()
