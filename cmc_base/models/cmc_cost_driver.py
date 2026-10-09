from odoo import fields, models


class CmcCostDriver(models.Model):
    _name = "cmc.cost.driver"
    _description = "Cost Driver (allocation base)"
    _order = "name"
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    active = fields.Boolean(default=True)
    note = fields.Text()
    measure_kind = fields.Selection(
        selection=[("allocation", "Allocation base"), ("statistical", "Statistical measure")],
        required=True,
        default="allocation",
    )
    measure_type = fields.Selection(
        selection=[("transaction", "Transaction"), ("duration", "Duration"), ("intensity", "Intensity")],
    )
    source = fields.Selection(
        selection=[("manual", "Manual"), ("feeder", "Feeder")],
        required=True,
        default="manual",
    )
    feeder_key = fields.Char(help="Key of the bridge feeder that loads the quantities of this measure.")

    _code_company_uniq = models.UniqueIndex(
        "(code, COALESCE(company_id, 0)) WHERE code IS NOT NULL",
        "The cost driver code must be unique per company "
        "(shared records included).",
    )
