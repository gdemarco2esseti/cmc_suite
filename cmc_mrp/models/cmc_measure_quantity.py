from odoo import api, models


class CmcMeasureQuantity(models.Model):
    _inherit = "cmc.measure.quantity"

    @api.model
    def _cmc_feeder_profiles(self):
        common = {
            "key": "mrp_produced_qty",
            "model": "stock.move",
            "date_field": "date",
            # why quantity: what was actually produced (done), not the demand; summed per move UoM
            # and converted to the product UoM by the engine
            "quantity": "quantity",
            "uom_field": "product_uom",
            "driver": "cmc_mrp.cmc_measure_produced_qty",
            "object_field": "product_id",
        }
        return super()._cmc_feeder_profiles() + [
            # why center required: MOs without CMC center are outside CMC product costing, on cost (MP, MOD) and quantity alike
            # finished moves of the order, byproducts excluded; why scrap_id: core 19 stamps production_id on the
            # scrap move of a finished product, a scrapped unit is not a second produced one (it feeds mrp_scrap_qty)
            {**common, "domain": [("state", "=", "done"), ("production_id", "!=", False), ("byproduct_id", "=", False),
                       ("scrap_id", "=", False), ("production_id.cmc_center_id", "!=", False)],
             "center_field": "production_id.cmc_center_id", "sign": 1},
            # scrapped finished units (spoilage of process costing), same grain and UoM conversion as the produced feeder
            {**common, "key": "mrp_scrap_qty", "driver": "cmc_mrp.cmc_measure_scrap_qty",
             "domain": [("state", "=", "done"), ("scrap_id", "!=", False), ("production_id.cmc_center_id", "!=", False)],
             "center_field": "production_id.cmc_center_id", "sign": 1},
            # an unbuild reverses a produced quantity (AD-111). why not consume_unbuild_id: core 19 never
            # sets it; the moves taking the finished product back out of stock carry unbuild_id and go to production
            {**common, "domain": [("state", "=", "done"), ("unbuild_id.mo_id.cmc_center_id", "!=", False),
                                  ("location_dest_id.usage", "=", "production"),
                                  ("origin_returned_move_id.production_id", "!=", False),
                                  ("origin_returned_move_id.byproduct_id", "=", False)],
             "center_field": "unbuild_id.mo_id.cmc_center_id", "sign": -1},
        ]
