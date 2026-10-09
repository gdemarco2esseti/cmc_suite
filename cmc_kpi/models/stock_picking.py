from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # why stored: the on-time delivery feeder groups on it, so its cost does not grow with the pickings
    cmc_on_time = fields.Boolean(
        string="Delivered On Time", compute="_compute_cmc_on_time", store=True,
        help="Done no later than the scheduled date.",
    )

    @api.depends("date_done", "scheduled_date")
    def _compute_cmc_on_time(self):
        for picking in self:
            picking.cmc_on_time = bool(picking.date_done and picking.date_done <= picking.scheduled_date)
