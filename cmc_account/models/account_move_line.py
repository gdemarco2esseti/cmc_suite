from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    # Durable home of a MANUAL cost classification.
    #
    # account.move.button_draft unlinks every analytic line of the entry
    # (account/models/account_move.py:6286) and re-posting recreates brand-new
    # records through _create_analytic_lines(). The analytic line is therefore a
    # DERIVED record: a controller's hand-made override cannot live there or a
    # single draft/re-post cycle destroys it without a trace. The journal item
    # survives the cycle, so the override is persisted here and re-applied by
    # account.analytic.line._cmc_auto_classify() when the line is recreated.
    cmc_classified_manually = fields.Boolean(
        string="CMC Manually Classified",
        copy=False,
        index="btree_not_null",
        help="A user classified the cost lines of this journal item by hand; "
        "the classification is restored when the entry is reset to draft and "
        "posted again.",
    )
    cmc_behavior = fields.Selection(
        selection=[
            ("fixed", "Fixed"),
            ("variable", "Variable"),
            ("semi_variable", "Semi-variable"),
        ],
        string="CMC Cost Behavior",
        copy=False,
    )
    cmc_directness = fields.Selection(
        selection=[("direct", "Direct"), ("indirect", "Indirect")],
        string="CMC Imputation",
        copy=False,
    )
    cmc_cost_element_id = fields.Many2one(
        "cmc.cost.element",
        string="CMC Cost Element",
        copy=False,
        ondelete="set null",
    )
