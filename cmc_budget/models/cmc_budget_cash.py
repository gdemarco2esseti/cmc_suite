from collections import defaultdict

from odoo import fields, models
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_utils import month_range, month_start

INVOICE_DAY = 15


class CmcBudgetCashRun(models.Model):
    _name = "cmc.budget.cash.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Budget Cash Forecast Run"
    _order = "id desc"
    _check_company_auto = True

    _cmc_input_fields = ("company_id", "version_id", "opening_balance", "customer_payment_term_id", "supplier_payment_term_id")
    _cmc_result_fields = ("line_ids",)

    name = fields.Char(required=True, default=lambda self: self.env._("Cash forecast"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    version_id = fields.Many2one(
        "cmc.plan.version", required=True, check_company=True, domain="[('version_type', 'in', ('budget', 'forecast'))]",
    )
    opening_balance = fields.Monetary()
    customer_payment_term_id = fields.Many2one("account.payment.term", help="Default terms of the revenue lines.")
    supplier_payment_term_id = fields.Many2one("account.payment.term", help="Default terms of the cost lines.")
    line_ids = fields.One2many("cmc.budget.cash.line", "run_id")

    def _cmc_compute(self):
        self.ensure_one()
        # raises unless the version is approved; locks it against a reset while this run is computed
        self._cmc_record_usage(self.version_id)
        flows = self._cmc_monthly_flows()
        version = self.version_id
        months = month_range(min([month_start(version.date_from), *flows]), max([month_start(version.date_to), *flows]))
        balance, vals = self.opening_balance, []
        for month in months:
            inflow, outflow = flows.get(month, (0.0, 0.0))
            balance += inflow + outflow
            vals.append({"run_id": self.id, "date": month, "inflow": inflow, "outflow": outflow, "net": inflow + outflow, "cumulative": balance})
        self.env["cmc.budget.cash.line"].create(vals)

    def _cmc_monthly_flows(self):
        """{due month: (inflow, outflow)}: every (term, kind, month) group of the lines is invoiced on the 15th and split by term line."""
        # why: a non-payment (depreciation) or a figurative cost never moves cash
        cash = Domain("kind", "=", "revenue") | Domain("element_id", "=", False) | (
            Domain("element_id.cmc_cash_excluded", "=", False) & Domain("element_id.cmc_kind", "!=", "figurative")
        )
        groups = self.env["cmc.budget.line"]._read_group(
            Domain("version_id", "=", self.version_id.id) & cash, ["date:month", "kind", "payment_term_id"], ["amount_company:sum"]
        )
        currency = self.company_id.currency_id
        flows = defaultdict(lambda: [0.0, 0.0])  # [inflow, outflow]; a cost share is already negative
        for month, kind, term, amount in groups:
            term = term or (self.customer_payment_term_id if kind == "revenue" else self.supplier_payment_term_id)
            invoice_date = month.replace(day=INVOICE_DAY)
            terms = term.line_ids.filtered(lambda line: line.value == "percent")
            paid = 0.0
            for index, term_line in enumerate(terms, 1):
                share = currency.round(amount * term_line.value_amount / 100.0) if index < len(terms) else currency.round(amount - paid)
                paid += share
                flows[month_start(term_line._get_due_date(invoice_date))][kind == "cost"] += share
            if not terms:
                flows[month][kind == "cost"] += amount
        return {month: tuple(flow) for month, flow in flows.items()}


class CmcBudgetCashLine(models.Model):
    _name = "cmc.budget.cash.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Budget Cash Forecast Result"
    _order = "run_id, date"

    run_id = fields.Many2one("cmc.budget.cash.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    date = fields.Date(required=True, help="First day of the month.")
    inflow = fields.Monetary()
    outflow = fields.Monetary()
    net = fields.Monetary()
    cumulative = fields.Monetary()
