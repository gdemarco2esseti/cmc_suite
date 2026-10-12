from odoo import api, models


class CmcMeasureQuantity(models.Model):
    _inherit = "cmc.measure.quantity"

    @api.model
    def _cmc_feeder_profiles(self):
        return super()._cmc_feeder_profiles() + [{
            "key": "account_customer_invoice_lines",
            "model": "account.move.line",
            "domain": [
                ("parent_state", "=", "posted"),
                ("move_id.move_type", "=", "out_invoice"),
                ("display_type", "=", "product"),
                ("partner_id", "!=", False),
            ],
            "date_field": "date",
            "quantity": "__count",
            "driver": "cmc_account.cmc_measure_customer_invoice_lines",
            "center_field": False,
            "object_field": "partner_id",
            "object_type": "customer",
        }]
