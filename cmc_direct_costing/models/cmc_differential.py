from odoo import api, fields, models


class CmcDifferentialAnalysis(models.TransientModel):
    _name = "cmc.differential.analysis"
    _description = "Differential / Relevant-Cost Analysis"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company
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
    volume = fields.Float(digits="Product Unit of Measure", default=1.0)
    make_unit_cost = fields.Monetary(string="Make Unit Cost (avoidable)")
    buy_unit_cost = fields.Monetary(string="Buy Unit Price")
    opportunity_cost = fields.Monetary()
    # drop line
    lost_contribution = fields.Monetary(string="Lost Contribution Margin")
    avoidable_fixed = fields.Monetary(string="Avoidable Fixed Cost")
    # replace
    new_relevant_cost = fields.Monetary(string="Keep — relevant cost")
    old_relevant_cost = fields.Monetary(string="Replace — relevant cost")

    differential = fields.Monetary(compute="_compute_result")
    verdict = fields.Char(compute="_compute_result")

    @api.depends(
        "decision_type", "volume", "make_unit_cost", "buy_unit_cost",
        "opportunity_cost", "lost_contribution", "avoidable_fixed",
        "new_relevant_cost", "old_relevant_cost",
    )
    def _compute_result(self):
        verdicts = {
            "make_or_buy": ("Buy", "Make"),
            "drop_line": ("Keep line", "Eliminate line"),
            "replace": ("Replace", "Keep machine"),
        }
        for rec in self:
            t = rec.decision_type
            if t == "make_or_buy":
                make = rec.make_unit_cost * rec.volume + rec.opportunity_cost
                rec.differential = make - rec.buy_unit_cost * rec.volume
            elif t == "drop_line":
                rec.differential = rec.lost_contribution - rec.avoidable_fixed
            else:  # replace
                rec.differential = rec.new_relevant_cost - rec.old_relevant_cost
            pos, neg = verdicts[t]
            if rec.differential > 0:
                rec.verdict = rec.env._(pos)
            elif rec.differential < 0:
                rec.verdict = rec.env._(neg)
            else:
                rec.verdict = rec.env._("Indifferent")
