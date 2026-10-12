from odoo import api, fields, models

from .cmc_utils import month_start


class CmcMonthMixin(models.AbstractModel):
    """Row keyed by a calendar month (AD-105): `date` is always its first day."""

    _name = "cmc.month.mixin"
    _description = "CMC monthly bucket"

    date = fields.Date(required=True, index=True, help="First day of the month.")

    @api.model
    def _cmc_month_vals(self, vals):
        if vals.get("date"):
            vals = {**vals, "date": month_start(fields.Date.to_date(vals["date"]))}
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._cmc_month_vals(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._cmc_month_vals(vals))
