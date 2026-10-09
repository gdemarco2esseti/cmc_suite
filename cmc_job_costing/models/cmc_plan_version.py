from odoo import fields, models


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    version_type = fields.Selection(selection_add=[("baseline", "Baseline")], ondelete={"baseline": "set default"})
