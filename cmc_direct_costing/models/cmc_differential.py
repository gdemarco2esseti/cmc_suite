from odoo import api, fields, models


class CmcDifferentialAnalysis(models.TransientModel):
    _name = "cmc.differential.analysis"
    _description = "Differential / Relevant-Cost Analysis"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    decision_type = fields.Selection(
        [
            ("make_or_buy", "Make or Buy"),
            ("drop_line", "Eliminate Product Line"),
            ("replace", "Replace Machine"),
        ],
        required=True,
        default="make_or_buy",
    )
    # make-or-buy
    volume = fields.Float(digits="Product Unit", default=1.0)
    make_unit_cost = fields.Monetary(string="Make Unit Cost (avoidable)")
    buy_unit_cost = fields.Monetary(string="Buy Unit Price")
    opportunity_cost = fields.Monetary()
    # drop line
    lost_contribution = fields.Monetary(string="Lost Contribution Margin")
    avoidable_fixed = fields.Monetary(string="Avoidable Fixed Cost")
    # replace: the names follow the labels, so an edit cannot flip the sign
    keep_relevant_cost = fields.Monetary(string="Keep — relevant cost")
    replace_relevant_cost = fields.Monetary(string="Replace — relevant cost")

    differential = fields.Monetary(compute="_compute_result")
    verdict = fields.Char(compute="_compute_result")

    def _verdicts(self, decision_type):
        """(verdict when differential > 0, verdict when < 0). The literals sit
        here so the translation extractor can see them."""
        if decision_type == "make_or_buy":
            return self.env._("Buy"), self.env._("Make")
        if decision_type == "drop_line":
            return self.env._("Keep line"), self.env._("Eliminate line")
        return self.env._("Replace"), self.env._("Keep machine")

    @api.depends(
        "decision_type", "volume", "make_unit_cost", "buy_unit_cost",
        "opportunity_cost", "lost_contribution", "avoidable_fixed",
        "keep_relevant_cost", "replace_relevant_cost",
    )
    def _compute_result(self):
        for rec in self:
            t = rec.decision_type
            if t == "make_or_buy":
                make = rec.make_unit_cost * rec.volume + rec.opportunity_cost
                rec.differential = make - rec.buy_unit_cost * rec.volume
            elif t == "drop_line":
                rec.differential = rec.lost_contribution - rec.avoidable_fixed
            else:  # replace
                rec.differential = (
                    rec.keep_relevant_cost - rec.replace_relevant_cost
                )
            pos, neg = rec._verdicts(t)
            # a few cents of rounding noise must not read as a confident verdict
            sign = rec.currency_id.compare_amounts(rec.differential, 0.0)
            if sign > 0:
                rec.verdict = pos
            elif sign < 0:
                rec.verdict = neg
            else:
                rec.verdict = rec.env._("Indifferent")
