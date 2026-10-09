from odoo import api, fields, models

from odoo.addons.cmc_base.models.account_analytic_line import CMC_AUTO

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
        twin_pos = [i for i, vals in enumerate(vals_list) if vals.get("move_line_id") in excluded]
        plain_pos = [i for i in range(len(vals_list)) if i not in set(twin_pos)]
        # only the twins take the superuser shortcut; the caller's other lines
        # are created as the caller, with every check
        plain = super().create([vals_list[i] for i in plain_pos]) if plain_pos else self.browse()
        twin_vals = [
            dict({k: v for k, v in vals_list[i].items() if k != "cmc_bridge_source"},
                 cmc_excluded=True, cmc_directness="indirect")
            for i in twin_pos
        ]
        # why sudo: cmc_excluded is superuser-only; the value is derived from
        # the journal item, never taken from the caller. The caller's own create
        # rights are checked before (ACL) and on the records created (rules):
        # the shortcut must not let a user create lines they could not create.
        self.check_access("create")
        # CMC_AUTO: the system values above are no manual classification
        twins = super(AccountAnalyticLine, self.sudo().with_context(cmc_auto_classify=CMC_AUTO)).create(twin_vals).with_env(self.env)
        twins.check_access("create")
        ids = [None] * len(vals_list)
        for pos, record in zip(plain_pos, plain):
            ids[pos] = record.id
        for pos, record in zip(twin_pos, twins):
            ids[pos] = record.id
        return self.browse(ids)

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

    @api.model
    def _cmc_bridge_profiles(self):
        # direct labor (MOD) booked by mrp_account on workcenter time
        return super()._cmc_bridge_profiles() + [{
            "key": "mrp_labour",
            "match": "_cmc_is_mrp_labour",
            "element": MOD_XMLID,
            "behavior": "variable",
            "directness": "direct",
        }]
