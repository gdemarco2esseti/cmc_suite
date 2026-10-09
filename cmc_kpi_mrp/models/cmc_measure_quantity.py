from odoo import api, models


class CmcMeasureQuantity(models.Model):
    _inherit = "cmc.measure.quantity"

    @api.model
    def _cmc_feeder_profiles(self):
        profiles = super()._cmc_feeder_profiles()
        done = [("state", "=", "done"), ("cmc_center_id", "!=", False)]
        # month of every MRP fact = month of close of the MO, also for the minutes logged earlier
        mo_grain = {"date_field": "production_id.date_finished", "center_field": "production_id.cmc_center_id",
                    "object_field": "production_id.product_id"}
        mo_done = [("production_id.state", "=", "done"), ("production_id.cmc_center_id", "!=", False)]
        mo_profile = {"model": "mrp.production", "date_field": "date_finished", "center_field": "cmc_center_id",
                      "object_field": "product_id", "domain": done}
        # rework output: the produced-quantity moves (same UoM rule, net of unbuilds) of the flagged MOs
        rework = []
        for profile in profiles:
            if profile["key"] == "mrp_produced_qty":
                path = "production_id" if profile["sign"] == 1 else "unbuild_id.mo_id"
                rework.append({**profile, "key": "mrp_rework_qty", "driver": "cmc_kpi_mrp.cmc_measure_rework_qty",
                               "domain": profile["domain"] + [(f"{path}.cmc_is_rework", "=", True)]})
        delivered = [("state", "=", "done"), ("picking_type_code", "=", "outgoing"), ("return_id", "=", False)]
        return profiles + rework + [
            {**mo_grain, "key": "mrp_wo_minutes", "model": "mrp.workorder", "domain": mo_done, "quantity": "duration",
             "driver": "cmc_kpi_mrp.cmc_measure_wo_minutes"},
            {**mo_grain, "key": "mrp_value_added_minutes", "model": "mrp.workcenter.productivity", "quantity": "duration",
             "domain": [("loss_type", "=", "productive")] + [(f"workorder_id.{leaf[0]}", *leaf[1:]) for leaf in mo_done],
             "date_field": "workorder_id.production_id.date_finished",
             "center_field": "workorder_id.production_id.cmc_center_id",
             "object_field": "workorder_id.production_id.product_id",
             "driver": "cmc_kpi_mrp.cmc_measure_value_added_minutes"},
            {**mo_profile, "key": "mrp_mo_lead_minutes", "quantity": "cmc_lead_time",
             "driver": "cmc_kpi_mrp.cmc_measure_mo_lead_minutes"},
            {**mo_profile, "key": "mrp_mo_done", "quantity": "__count", "driver": "cmc_kpi_mrp.cmc_measure_mo_done"},
            {"key": "stock_delivery_lead_minutes", "model": "stock.picking", "domain": delivered,
             "date_field": "date_done", "quantity": "cmc_delivery_lead_time",
             "driver": "cmc_kpi_mrp.cmc_measure_delivery_lead_minutes"},
        ]
