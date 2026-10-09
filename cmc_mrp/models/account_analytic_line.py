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

    @api.model_create_multi
    def create(self, vals_list):
        # why here: the twin must be excluded BEFORE the classification that
        # runs at the end of create, and core rebuilds analytic lines in many
        # places (repost, distribution edit, reversal); the journal item is the
        # one record that survives all of them.
        item_ids = {vals["move_line_id"] for vals in vals_list if vals.get("move_line_id")}
        # why sudo: read of a CMC flag by whoever posts the entry
        excluded = set(
            self.env["account.move.line"].sudo().browse(item_ids).filtered("cmc_excluded").ids
        )
        if not excluded:
            return super().create(vals_list)
        vals_list = [
            dict(vals, cmc_excluded=True, cmc_directness="indirect")
            if vals.get("move_line_id") in excluded else vals
            for vals in vals_list
        ]
        # why sudo: cmc_excluded is superuser-only; the value is derived from
        # the journal item, never taken from the caller. The caller's own create
        # rights are checked before (ACL) and on the records created (rules):
        # the shortcut must not let a user create lines they could not create.
        self.check_access("create")
        records = super(AccountAnalyticLine, self.sudo()).create(vals_list).with_env(self.env)
        records.check_access("create")
        return records

    @api.model
    def _cmc_classify_trigger_fields(self):
        return super()._cmc_classify_trigger_fields() | {"category"}

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
            if (
                line.cmc_classified_manually
                or line.cmc_excluded
                or not line._cmc_is_mrp_labour()
            ):
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
        return super()._cmc_auto_classify()
