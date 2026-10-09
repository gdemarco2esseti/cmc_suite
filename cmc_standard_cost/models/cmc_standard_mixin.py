from odoo import api, fields, models
from odoo.exceptions import ValidationError

# why: unit prices, rates and per-unit quantities (minutes/hours, BoM fractions) lose accuracy at 2 digits
PRECISE = (16, 6)


class CmcStandardLineMixin(models.AbstractModel):
    """Input table of a standard version: locked with the version, refused on any other version type (AD-104)."""

    _name = "cmc.standard.line.mixin"
    _inherit = ["cmc.run.line.mixin"]
    _description = "CMC standard version table"
    _check_company_auto = True
    _cmc_run_field = "version_id"

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)

    @api.constrains("version_id")
    def _check_version_standard(self):
        if self.version_id.filtered(lambda version: version.version_type != "standard"):
            raise ValidationError(self.env._("Standard cost data live on a version of type Standard."))
