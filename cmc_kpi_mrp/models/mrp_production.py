from odoo import api, fields, models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    cmc_is_rework = fields.Boolean(
        string="Rework", copy=False, help="Re-processes units that failed the first pass (first-pass yield KPI)."
    )
    cmc_date_released = fields.Datetime(
        string="Released On", copy=False, readonly=True,
        help="Release to the shop floor: stamped at confirmation. Start of the manufacturing lead time.",
    )
    # why stored: the lead-time feeder groups on it, so its cost does not grow with the orders
    cmc_lead_time = fields.Float(
        string="Lead Time (min)", compute="_compute_cmc_lead_time", store=True,
        help="Minutes from release (else creation) to close of a done order.",
    )

    @api.depends("state", "date_finished", "cmc_date_released", "create_date")
    def _compute_cmc_lead_time(self):
        for production in self:
            start = production.cmc_date_released or production.create_date
            if production.state != "done" or not production.date_finished or not start:
                production.cmc_lead_time = 0.0
                continue
            production.cmc_lead_time = max((production.date_finished - start).total_seconds() / 60, 0.0)

    def _get_backorder_mo_vals(self):
        # why: a backorder is the same job, so rework flag and release date (lead time) carry over
        vals = super()._get_backorder_mo_vals()
        vals.update(cmc_is_rework=self.cmc_is_rework, cmc_date_released=self.cmc_date_released)
        return vals

    def action_confirm(self):
        # why not date_start: core rewrites it at the first work-order start/finish, so it is not a release
        self.filtered(lambda mo: not mo.cmc_date_released).cmc_date_released = fields.Datetime.now()
        return super().action_confirm()
