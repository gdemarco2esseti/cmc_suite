from odoo import fields, models


class CmcManualClassification(models.Model):
    _name = "cmc.manual.classification"
    _description = "CMC Manual Classification"

    # Durable home of a MANUAL cost classification.
    #
    # account.move.button_draft unlinks every analytic line of the entry and
    # re-posting recreates brand-new records, so the override cannot live on the
    # (derived) analytic line. The journal item survives, but it fans out 1:N to
    # analytic lines (split distribution): the key is therefore the pair
    # (journal item, cost center of the analytic line).
    move_line_id = fields.Many2one(
        "account.move.line", required=True, index=True, ondelete="cascade"
    )
    center_id = fields.Many2one(
        "account.analytic.account", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="move_line_id.company_id", store=True, index=True
    )
    cmc_cost_element_id = fields.Many2one(
        "cmc.cost.element", string="Cost Element", ondelete="set null"
    )
    cmc_behavior = fields.Selection(
        selection=lambda self: self.env["account.analytic.line"]._fields["cmc_behavior"]._description_selection(self.env),
        string="Cost Behavior",
    )
    cmc_directness = fields.Selection(
        selection=lambda self: self.env["account.analytic.line"]._fields["cmc_directness"]._description_selection(self.env),
        string="Imputation",
    )

    _move_center_uniq = models.UniqueIndex("(move_line_id, center_id)")
