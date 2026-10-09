import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)

MP_XMLID = "cmc_base.cmc_element_mp"


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    cmc_center_id = fields.Many2one(
        "account.analytic.account",
        string="CMC Cost Center",
        index="btree_not_null",
        check_company=True,
        domain="['&', ('company_id', 'in', [False, company_id]),"
        " '|', ('cmc_center_type', '!=', False), ('cmc_is_period', '=', True)]",
        help="Cost center that absorbs the direct material (MP) cost of this "
        "manufacturing order in management control. Must belong to the root "
        "analytic plan and to the company of the order.",
    )

    @api.constrains("cmc_center_id", "company_id")
    def _check_cmc_center_id(self):
        """Explicit company + plan check on the cost center.

        ``mrp.production`` does not declare ``_check_company_auto``, so the
        ``check_company=True`` above is inert on its own, and the cost lines are
        posted through ``sudo()`` so the analytic record rule never gets a
        chance to block a cross-company center either. Enforcing it here is the
        only thing standing between a mis-typed center and cross-company cost
        leakage. The analytic-plan check lives here too: it is a CONTROLLING
        misconfiguration, it must surface when the controller sets the center,
        not in the face of a shop-floor operator closing the order.
        """
        for production in self:
            center = production.cmc_center_id
            if not center:
                continue
            if center.company_id and center.company_id != production.company_id:
                raise ValidationError(self.env._(
                    "CMC cost center %(center)s belongs to company "
                    "%(center_company)s and cannot absorb the cost of a "
                    "manufacturing order of %(company)s.",
                    center=center.display_name,
                    center_company=center.company_id.display_name,
                    company=production.company_id.display_name,
                ))
            if center.plan_id._column_name() != "account_id":
                raise ValidationError(self.env._(
                    "CMC cost center %(center)s must belong to the root "
                    "analytic plan to absorb manufacturing cost.",
                    center=center.display_name,
                ))

    def _post_inventory(self, cancel_backorder=False):
        res = super()._post_inventory(cancel_backorder=cancel_backorder)
        self.filtered(lambda mo: mo.state == "done")._cmc_create_material_lines()
        return res

    def _post_labour(self):
        res = super()._post_labour()
        # why sudo: the shop-floor user closing the order has no CMC/analytic
        # rights; only CMC metadata is written. Core posts the entry (creating
        # its analytic lines, auto-classified on the way) and links the times
        # only afterwards, so the twin is flagged here, entry-wide (expense AND
        # WIP side), and the classification already applied is wiped.
        twins = (
            self.sudo()
            .workorder_ids.time_ids.account_move_line_id.move_id.line_ids.analytic_line_ids
        )
        if twins:
            twins.with_context(cmc_auto_classify=True).write({
                "cmc_excluded": True,
                "cmc_behavior": False,
                "cmc_cost_element_id": False,
                "cmc_directness": "indirect",
            })
        return res

    def _cmc_create_material_lines(self):
        """Book the direct material (MP) cost of the consumed moves on the MO
        cost center, valued at the move's real SVL value.

        NOT always by creating a line. ``stock_account`` already creates an
        analytic line for a done move carrying an analytic distribution
        (``_create_analytic_move``, stock_account/models/stock_move.py:223), and
        ``mrp_account`` registers an ``account.analytic.applicability`` for
        manufacturing orders so distributions get applied to MOs automatically.
        Whenever core has already booked a line on OUR center, we CLASSIFY and
        adopt that line instead of creating a parallel one -- creating a second
        line would double-count every consumption for any customer who links
        manufacturing orders to projects or plans with an applicability.

        Idempotent (one indexed lookup of the already-booked moves), batched,
        and posted as sudo with the production's company.
        """
        AL = self.env["account.analytic.line"]
        mp = AL._cmc_element_ref(MP_XMLID)
        all_moves = self.move_raw_ids
        if not all_moves:
            return True
        # why sudo: CMC bookkeeping lookup; a shop-floor user closing an order
        # has no read access to analytic lines.
        booked_move_ids = set(
            AL.sudo().search(
                [("cmc_mrp_move_id", "in", all_moves.ids)]
            ).cmc_mrp_move_id.ids
        )
        vals_list = []
        adopted_vals = []
        for production in self:
            center = production.cmc_center_id
            if not center:
                continue
            if center.plan_id._column_name() != "account_id":
                # a constraint already refuses this at configuration time; if a
                # legacy record slipped through, do not block the shop floor.
                _logger.warning(
                    "CMC: manufacturing order %s points at cost center %s, which "
                    "is not on the root analytic plan: no material cost booked.",
                    production.name, center.display_name,
                )
                continue
            rounding = production.company_id.currency_id.rounding
            # core books the cost at the source document's date; falling back to
            # today would push an order closed in the next period into the wrong
            # month and out of the cmc.capacity.analysis window.
            date = (production.date_finished or fields.Datetime.now()).date()
            for move in production.move_raw_ids:
                if move.state != "done" or move.id in booked_move_ids:
                    continue
                value = move.value or 0.0
                if float_is_zero(value, precision_rounding=rounding):
                    continue
                # why sudo: read-only check of the lines core already booked.
                core_lines = move.sudo().analytic_account_line_ids.filtered(
                    lambda line: line.cmc_center_id == center
                )
                if core_lines:
                    for line in core_lines:
                        adopted_vals.append(
                            (line.id, self._cmc_adopt_vals(line, move, mp))
                        )
                    continue
                vals_list.append({
                    "name": move.product_id.display_name or production.name,
                    "account_id": center.id,
                    "company_id": production.company_id.id,
                    "date": date,
                    "unit_amount": move._get_valued_qty(),
                    "amount": -value,  # natural SVL sign (returns credit MP)
                    "product_id": move.product_id.id,
                    "ref": production.name,
                    "cmc_mrp_move_id": move.id,
                    "cmc_cost_element_id": mp.id if mp else False,
                    "cmc_behavior": "variable",
                    "cmc_directness": "direct",
                })
        if adopted_vals:
            # why sudo: same reason as the create below -- the order is closed by
            # a shop-floor user with no rights on analytic lines.
            AL.sudo()._cmc_write_batched(adopted_vals)
        if vals_list:
            # why sudo: a mrp.group_mrp_user closing an order has no create
            # rights on account.analytic.line; the lines are system-derived.
            AL.sudo().with_context(cmc_auto_classify=True).create(vals_list)
        return True

    @api.model
    def _cmc_adopt_vals(self, line, move, element):
        """Classification to stamp on a core-created analytic line we adopt."""
        vals = {"cmc_mrp_move_id": move.id}
        if line.cmc_classified_manually:
            return vals
        # the element is FORCED, not filled: the line is a raw-material
        # consumption whatever the account mapping or a rule may have guessed.
        if element:
            vals["cmc_cost_element_id"] = element.id
        if not line.cmc_behavior:
            vals["cmc_behavior"] = "variable"
        if line.cmc_directness != "direct":
            vals["cmc_directness"] = "direct"
        return vals
