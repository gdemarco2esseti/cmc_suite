from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from .account_move_line import EXPENSE_TYPES, REVENUE_TYPES

RESULT_FIELDS = ("coge_amount", "coan_amount", "figurative_amount", "neutral_amount", "unexplained_amount")


class CmcCogeReconciliation(models.Model):
    _name = "cmc.coge.reconciliation"
    _description = "COGE - COAN reconciliation"
    _inherit = ["cmc.run.mixin"]
    _order = "date_to desc, id desc"

    _cmc_input_fields = ("company_id", "date_from", "date_to")

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    coge_amount = fields.Monetary(string="General Ledger Cost", readonly=True, copy=False)
    coan_amount = fields.Monetary(string="Management Accounting Cost", readonly=True, copy=False)
    figurative_amount = fields.Monetary(readonly=True, copy=False)
    neutral_amount = fields.Monetary(string="Neutral (no analytic)", readonly=True, copy=False)
    unexplained_amount = fields.Monetary(readonly=True, copy=False)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        if any(rec.date_from > rec.date_to for rec in self):
            raise ValidationError(self.env._("The start date must not be after the end date."))

    def write(self, vals):
        if not self._cmc_state_unlocked() and not set(RESULT_FIELDS).isdisjoint(vals):
            raise UserError(self.env._("The results of a run are written by Compute only."))
        return super().write(vals)

    def _cmc_compute(self):
        Line = self.env["account.move.line"]
        AnalyticLine = self.env["account.analytic.line"]
        for rec in self:
            company = rec.company_id
            gl = Line._cmc_posted_domain(company, rec.date_from, rec.date_to) & Domain("account_id.account_type", "in", EXPENSE_TYPES)
            # C-3: `= False` is refused by analytic.mixin search, `in [False]` is the supported form
            neutral = gl & Domain("analytic_distribution", "in", [False])
            scope = Line._cmc_reader_scope(company)
            (coge,) = scope._read_group(gl, aggregates=["balance:sum"])[0]
            (neutral_amount,) = scope._read_group(neutral, aggregates=["balance:sum"])[0]
            coan_domain = Domain([
                ("company_id", "=", company.id), ("cmc_excluded", "=", False),
                ("date", ">=", rec.date_from), ("date", "<=", rec.date_to),
            ]) & (Domain("general_account_id", "=", False) | Domain("general_account_id.account_type", "not in", REVENUE_TYPES))
            # why sudo: the controller reconciles without read access to every analytic line
            analytic = AnalyticLine.sudo()
            (coan,) = analytic._cmc_cost_read_group(coan_domain)[0]
            (figurative,) = analytic._cmc_cost_read_group(coan_domain & Domain("cmc_cost_element_id.cmc_kind", "=", "figurative"))[0]
            coge, neutral_amount = coge or 0.0, neutral_amount or 0.0
            rec.write({
                "coge_amount": coge, "coan_amount": coan, "figurative_amount": figurative,
                "neutral_amount": neutral_amount, "unexplained_amount": coge - (coan - figurative + neutral_amount),
            })

    def _cmc_reset_values(self):
        return dict.fromkeys(RESULT_FIELDS, 0.0)
