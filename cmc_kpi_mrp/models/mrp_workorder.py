from odoo import api, fields, models
from odoo.tools.intervals import Intervals


class MrpWorkorder(models.Model):
    _inherit = "mrp.workorder"

    # why stored: the value-added feeder sums it in SQL, without loading the time entries
    cmc_productive_minutes = fields.Float(
        string="Productive Minutes", compute="_compute_cmc_productive_minutes", store=True,
        help="Productive time entries merged: overlapping timers of concurrent operators count once.",
    )

    @api.depends("time_ids.date_start", "time_ids.date_end", "time_ids.loss_type")
    def _compute_cmc_productive_minutes(self):
        for workorder in self:
            # same merge as core _intervals_duration; running timers (no end) do not count yet
            times = workorder.time_ids.filtered(lambda t: t.loss_type == "productive" and t.date_start and t.date_end)
            workorder.cmc_productive_minutes = sum(
                (stop - start).total_seconds() / 60 for start, stop, _timers in Intervals([(t.date_start, t.date_end, t) for t in times])
            )
