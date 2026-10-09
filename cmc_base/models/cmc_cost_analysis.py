from odoo import fields, models


class CmcCostElement(models.Model):
    _name = "cmc.cost.element"
    _description = "Cost Element (nature)"
    _order = "sequence, name"
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    cmc_kind = fields.Selection(
        selection=[("real", "Real (contabile)"), ("figurative", "Figurative (co.an.)")],
        string="Kind",
        default="real",
        required=True,
        help="Figurative elements never reach co.ge.; they enter only the "
        "economico-tecnico cost level.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    _code_company_uniq = models.UniqueIndex(
        "(code, COALESCE(company_id, 0)) WHERE code IS NOT NULL",
        "The cost element code must be unique per company "
        "(shared records included).",
    )


class CmcCostAnalysisLine(models.Model):
    _name = "cmc.cost.analysis.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Cost Analysis Matrix Line"
    _order = "run_id, state, center_id"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.closing.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    center_id = fields.Many2one(
        # restrict, as on cmc.closing.line: results of a computed closing stay
        "account.analytic.account", required=True, index=True, ondelete="restrict"
    )
    center_type = fields.Selection(related="center_id.cmc_center_type", store=True)
    element_id = fields.Many2one(
        "cmc.cost.element", string="Cost Element", index=True, check_company=True
    )
    state = fields.Selection(
        selection=[("pre", "Before Closing"), ("post", "After Closing")],
        required=True,
        index=True,
    )
    amount = fields.Monetary()
