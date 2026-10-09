from odoo import models


class MrpWorkorder(models.Model):
    _inherit = "mrp.workorder"

    def _create_or_update_analytic_entry(self):
        res = super()._create_or_update_analytic_entry()
        self._cmc_stamp_cost_object()
        return res

    def _cmc_stamp_cost_object(self):
        """Workcenter (MOD) lines belong to the finished product of the order (AD-106)."""
        # why sudo: the operator timing the work order has no analytic/CMC rights
        # why center required: an MO without CMC center is outside CMC product costing, as for MP lines and the feeder
        orders = self.sudo().filtered(lambda wo: wo.wc_analytic_account_line_ids and wo.production_id.cmc_center_id)
        object_ids = orders.production_id._cmc_product_object_ids()
        for object_id, group in orders.grouped(lambda wo: object_ids[wo.production_id.id]).items():
            lines = group.wc_analytic_account_line_ids.filtered(lambda line: line.cmc_cost_object_id.id != object_id)
            if lines:
                lines.write({"cmc_cost_object_id": object_id})
