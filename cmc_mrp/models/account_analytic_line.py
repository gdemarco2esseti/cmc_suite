from odoo import api, fields, models

MOD_XMLID = "cmc_base.cmc_element_mod"


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_mrp_move_id = fields.Many2one(
        "stock.move",
        string="CMC Consumption Move",
        index="btree_not_null",
        ondelete="set null",  # never destroy an accounting-derived line we merely adopted
        copy=False,
        help="Manufacturing consumption move this management-control material "
        "cost line was posted from (or adopted for); the link is what prevents "
        "double imputation.",
    )

    @api.model
    def _cmc_classify_trigger_fields(self):
        return super()._cmc_classify_trigger_fields() | {"category"}

    def _cmc_mrp_labour_duplicates(self):
        """Analytic lines that would count manufacturing labor TWICE.

        ``mrp_account`` books workcenter time twice on purpose: once as an
        analytic line on the workorder (mrp_workorder.py:55) and once as a
        journal entry on the production-cost account (_post_labour,
        mrp_account/models/mrp_production.py:125-137). If that account carries
        an analytic distribution, posting the entry creates a SECOND analytic
        line -- same hours, same money -- which our rules would happily
        classify as a cost. The journal-entry side is identifiable because
        _post_labour stamps its journal items onto mrp.workcenter.productivity
        records, so those lines are excluded from classification and never
        enter the cost base.
        """
        move_lines = self.move_line_id
        if not move_lines:
            return self.browse()
        # why sudo: read-only duplicate detection; mrp.workcenter.productivity
        # is not readable by a controller, and a shop-floor user posting the
        # entry has no rights on CMC data either.
        duplicated = set(
            self.env["mrp.workcenter.productivity"].sudo().search(
                [("account_move_line_id", "in", move_lines.ids)]
            ).account_move_line_id.ids
        )
        return self.filtered(lambda line: line.move_line_id.id in duplicated)

    def _cmc_is_mrp_labour(self):
        """A native manufacturing-order LABOR line.

        ``category == 'manufacturing_order'`` alone is not a labor test: with
        ``project_mrp_account`` installed, the raw-material consumption moves of
        an MO get the very same category
        (project_mrp_account/models/stock_move.py:17). Keying on the category
        alone books MATERIALS as direct LABOR -- the prime cost would be right
        in total and wrong in every one of its two components.

        What actually separates the two is the unit: ``mrp_account`` always
        books workcenter time in hours (mrp_account/models/mrp_workorder.py:35),
        while a consumption line is always booked in the product's own unit.
        """
        self.ensure_one()
        if self.category != "manufacturing_order":
            return False
        if not self.product_uom_id:
            # core always stamps the product's unit on a consumption line, so a
            # unit-less manufacturing line cannot be material.
            return True
        # same reference mrp_account itself uses to stamp the unit
        return self.product_uom_id == self.env.ref("uom.product_uom_hour")

    def _cmc_auto_classify(self):
        """Enrich native manufacturing-order labor lines (created by mrp_account
        with category 'manufacturing_order') as direct labor (MOD). Fill-only
        and manual-safe; delegates the rest to the financial-account engine."""
        mod = self._cmc_element_ref(MOD_XMLID)
        vals_by_line = []
        for line in self:
            if line.cmc_classified_manually or not line._cmc_is_mrp_labour():
                continue
            vals = {}
            if mod and not line.cmc_cost_element_id:
                vals["cmc_cost_element_id"] = mod.id
            if not line.cmc_behavior:
                vals["cmc_behavior"] = "variable"
            if line.cmc_directness != "direct":
                vals["cmc_directness"] = "direct"
            if vals:
                vals_by_line.append((line.id, vals))
        self._cmc_write_batched(vals_by_line)
        # the labour journal-entry twins never reach the rule engine
        return super(
            AccountAnalyticLine, self - self._cmc_mrp_labour_duplicates()
        )._cmc_auto_classify()
