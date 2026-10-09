from odoo import api, models


class CmcMeasureQuantity(models.Model):
    _inherit = "cmc.measure.quantity"

    @api.model
    def _cmc_feeder_profiles(self):
        delivered = [("state", "=", "done"), ("picking_type_code", "=", "outgoing")]
        shipments = {"model": "stock.picking", "date_field": "date_done", "quantity": "__count"}
        return super()._cmc_feeder_profiles() + [
            {
                "key": "stock_scrap_qty", "model": "stock.scrap", "domain": [("state", "=", "done")],
                "date_field": "date_done", "quantity": "scrap_qty", "uom_field": "product_uom_id",
                "driver": "cmc_kpi.cmc_measure_scrap_qty", "object_field": "product_id",
            },
            {**shipments, "key": "stock_otd_shipped", "domain": delivered, "driver": "cmc_kpi.cmc_measure_shipments"},
            {
                **shipments, "key": "stock_otd_on_time", "domain": delivered + [("cmc_on_time", "=", True)],
                "driver": "cmc_kpi.cmc_measure_shipments_on_time",
            },
        ]
