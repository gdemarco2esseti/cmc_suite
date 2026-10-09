import pytz

from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # why stored: the on-time delivery feeder groups on it, so its cost does not grow with the pickings
    cmc_on_time = fields.Boolean(
        string="Delivered On Time", compute="_compute_cmc_on_time", store=True,
        help="Done no later than the day of the deadline (else of the scheduled date), in the company timezone.",
    )

    @api.depends("state", "date_done", "date_deadline", "scheduled_date", "company_id.partner_id.tz")
    def _compute_cmc_on_time(self):
        for picking in self:
            promised = picking.date_deadline or picking.scheduled_date
            if picking.state != "done" or not picking.date_done or not promised:
                picking.cmc_on_time = False
                continue
            tz = pytz.timezone(picking.company_id.partner_id.tz or "UTC")
            # why: the promise is a day, not an instant; the day is the one of the company
            day = lambda moment: pytz.utc.localize(moment).astimezone(tz).date()  # noqa: E731
            picking.cmc_on_time = day(picking.date_done) <= day(promised)
