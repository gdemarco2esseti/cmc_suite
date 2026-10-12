from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # why stored: the delivery lead-time feeder groups on it, so its cost does not grow with the transfers
    cmc_delivery_lead_time = fields.Float(
        string="Delivery Lead Time (min)", compute="_compute_cmc_delivery_lead_time", store=True,
        help="Minutes from the sale order date (else creation) to the validation of a done transfer.",
    )

    # why no sale_id.date_order: sale_stock is optional; the order date is read once the transfer is done
    @api.depends("state", "date_done", "create_date")
    def _compute_cmc_delivery_lead_time(self):
        has_sale = "sale_id" in self._fields
        for picking in self:
            start = (has_sale and picking.sale_id.date_order) or picking.create_date
            if picking.state != "done" or not picking.date_done or not start:
                picking.cmc_delivery_lead_time = 0.0
                continue
            picking.cmc_delivery_lead_time = max((picking.date_done - start).total_seconds() / 60, 0.0)
