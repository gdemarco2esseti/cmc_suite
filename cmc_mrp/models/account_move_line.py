from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.cmc_base.models.account_analytic_line import CMC_AUTO


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    # copy=True on purpose: a reversal must stay excluded, and core builds it
    # from copy_data.
    cmc_excluded = fields.Boolean(
        string="Excluded from Cost Base",
        readonly=True,
        help="Its analytic lines are system twins of a cost already counted "
        "elsewhere (manufacturing labour): core rebuilds analytic lines on "
        "repost, distribution edit and reversal, this flag is what re-excludes "
        "them.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        # the flag is system-only. Copies (reversal, duplicate) carry it in
        # their vals: only a reversal run by the system keeps it, anything
        # else is dropped rather than refused, so duplicating an entry works.
        if not self.env.su and self.env.context.get("cmc_auto_classify") is not CMC_AUTO:
            vals_list = [
                {k: v for k, v in vals.items() if k != "cmc_excluded"} for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        if "cmc_excluded" in vals and not self.env.su:
            raise AccessError(self.env._("The cost base exclusion is set by the system only."))
        return super().write(vals)
