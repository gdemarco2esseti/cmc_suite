from odoo import Command, fields, models
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    cmc_center_id = fields.Many2one(
        "account.analytic.account",
        string="CMC Cost Center",
        domain="['|', ('cmc_center_type', '!=', False), ('cmc_is_period', '=', True)]",
        help="Cost center that absorbs the direct material (MP) cost of this "
        "manufacturing order in management control. Must belong to the root "
        "analytic plan.",
    )

    def _post_inventory(self, cancel_backorder=False):
        res = super()._post_inventory(cancel_backorder=cancel_backorder)
        self.filtered(lambda mo: mo.state == "done")._cmc_create_material_lines()
        return res

    def _cmc_create_material_lines(self):
        """Create one classified MP (raw material) cost line per consumed move,
        valued at the move's real SVL value. Idempotent (a move already linked to
        a CMC line is skipped), batched, and posted as sudo with the production's
        company so a shop-floor user without analytic-line rights can mark the MO
        done."""
        AL = self.env["account.analytic.line"].sudo()
        mp = self.env.ref("cmc_base.cmc_element_mp", raise_if_not_found=False)
        for production in self:
            center = production.cmc_center_id
            if not center:
                continue
            if center.plan_id._column_name() != "account_id":
                raise UserError(self.env._(
                    "CMC cost center '%s' must belong to the root analytic plan "
                    "to absorb manufacturing cost.", center.display_name,
                ))
            vals_list, moves = [], []
            for move in production.move_raw_ids:
                if move.state != "done" or move.cmc_mrp_analytic_line_ids:
                    continue
                value = move.value or 0.0
                if not value:
                    continue
                vals_list.append({
                    "name": move.product_id.display_name or production.name,
                    "account_id": center.id,
                    "company_id": production.company_id.id,
                    "unit_amount": move._get_valued_qty(),
                    "amount": -value,  # natural SVL sign (returns credit MP)
                    "product_id": move.product_id.id,
                    "ref": production.name,
                    "cmc_cost_element_id": mp.id if mp else False,
                    "cmc_behavior": "variable",
                    "cmc_directness": "direct",
                })
                moves.append(move)
            if not vals_list:
                continue
            lines = AL.with_context(cmc_auto_classify=True).create(vals_list)
            for move, line in zip(moves, lines):
                move.sudo().cmc_mrp_analytic_line_ids = [Command.link(line.id)]
        return True
