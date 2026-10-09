from collections import defaultdict

from odoo import fields, models
from odoo.fields import Domain

from .cmc_utils import month_start


class CmcResponsibilityCenter(models.Model):
    _name = "cmc.responsibility.center"
    _description = "Responsibility Center"
    _order = "name"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    active = fields.Boolean(default=True)
    responsibility_type = fields.Selection(
        selection=[("cost", "Cost"), ("revenue", "Revenue"), ("profit", "Profit"), ("investment", "Investment")],
        required=True,
        default="cost",
    )
    owner_id = fields.Many2one("res.users", string="Responsible", required=True, index=True)
    center_ids = fields.Many2many(
        "account.analytic.account",
        "cmc_responsibility_center_account_rel",
        "responsibility_id",
        "center_id",
        string="Cost Centers",
        check_company=True,
    )
    capital_ids = fields.One2many("cmc.responsibility.capital", "responsibility_id", string="Invested Capital")

    def _cmc_responsibility_summary(self, date_from, date_to):
        """{id: figures} over the cost centers of each record and their descendants."""
        self.check_access("read")
        # why sudo: the figures are aggregates over the perimeter the caller owns (checked above);
        # the lines' own rules would hide most of them from a read-only responsible
        recs = self.sudo()
        subtrees = recs.center_ids._cmc_center_subtrees()
        result = {}
        for company, company_recs in recs.grouped("company_id").items():
            centers = company_recs.center_ids._cmc_center_descendants()
            cost, revenue = self._cmc_center_figures(centers, company, date_from, date_to)
            capital = dict(
                self.env["cmc.responsibility.capital"].sudo()._read_group(
                    Domain("responsibility_id", "in", company_recs.ids)
                    & Domain("date", ">=", month_start(date_from)) & Domain("date", "<=", date_to),
                    ["responsibility_id"],
                    ["amount:avg"],
                )
            )
            for rec in company_recs:
                nodes = set().union(*(subtrees[center.id] for center in rec.center_ids))
                controllable = sum(cost.get((node, True), 0.0) for node in nodes)
                non_controllable = sum(cost.get((node, False), 0.0) for node in nodes)
                total_revenue = sum(revenue.get(node, 0.0) for node in nodes)
                outcome = total_revenue - controllable - non_controllable
                invested = capital.get(rec, 0.0)
                result[rec.id] = {
                    "controllable_cost": controllable,
                    "non_controllable_cost": non_controllable,
                    "revenue": total_revenue,
                    "result": outcome,
                    "invested_capital": invested,
                    "roi": outcome / invested if rec.responsibility_type == "investment" and invested else 0.0,
                }
        return result

    def _cmc_center_figures(self, centers, company, date_from, date_to):
        """({(center id, controllable): cost}, {center id: revenue}) in two reads per analytic plan.
        Cost = -amount of the debit lines and of the allocation lines (a relocation nets out);
        revenue = the other credit lines (an allocation counter-line is not revenue)."""
        Line = self.env["account.analytic.line"].sudo()
        base = (
            Domain("cmc_excluded", "=", False) & Domain("company_id", "in", [False, company.id])
            & Domain("date", ">=", date_from) & Domain("date", "<=", date_to)
        )
        is_cost = Domain("amount", "<", 0) | Line._cmc_relocation_domain()
        cost, revenue = defaultdict(float), defaultdict(float)
        for plan, accounts in centers.grouped("plan_id").items():
            if not plan:
                continue
            col = plan._column_name()
            scope = base & Domain(col, "in", accounts.ids)
            for center, element, amount in Line._cmc_cost_read_group(scope & is_cost, groupby=[col, "cmc_cost_element_id"]):
                # no element = unclassified = not controllable
                cost[center.id, bool(element and element.cmc_controllable)] += amount
            for center, amount in Line._read_group(scope & ~is_cost, [col], ["amount:sum"]):
                revenue[center.id] += amount
        return cost, revenue


class CmcResponsibilityCapital(models.Model):
    _name = "cmc.responsibility.capital"
    _inherit = ["cmc.month.mixin"]
    _description = "Invested Capital of a Responsibility Center"
    _order = "date desc"
    _check_company_auto = True

    responsibility_id = fields.Many2one("cmc.responsibility.center", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="responsibility_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="responsibility_id.currency_id")
    amount = fields.Monetary(string="Invested Capital")

    _responsibility_month_uniq = models.UniqueIndex(
        "(responsibility_id, date)", "Invested capital is typed once per month and responsibility center."
    )


class CmcResponsibilitySummary(models.TransientModel):
    _name = "cmc.responsibility.summary"
    _description = "Controllable Cost and ROI Report"

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    line_ids = fields.One2many("cmc.responsibility.summary.line", "summary_id")

    def action_compute(self):
        self.ensure_one()
        # the record rules show each user his own responsibility centers
        resps = self.env["cmc.responsibility.center"].search([])
        figures = resps._cmc_responsibility_summary(self.date_from, self.date_to)
        self.line_ids = [
            fields.Command.create({"responsibility_id": resp.id, **figures[resp.id]}) for resp in resps
        ]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class CmcResponsibilitySummaryLine(models.TransientModel):
    _name = "cmc.responsibility.summary.line"
    _description = "Controllable Cost and ROI Report Line"

    summary_id = fields.Many2one("cmc.responsibility.summary", required=True, ondelete="cascade")
    responsibility_id = fields.Many2one("cmc.responsibility.center", required=True)
    responsibility_type = fields.Selection(related="responsibility_id.responsibility_type")
    owner_id = fields.Many2one(related="responsibility_id.owner_id")
    currency_id = fields.Many2one(related="responsibility_id.currency_id")
    controllable_cost = fields.Monetary()
    non_controllable_cost = fields.Monetary()
    revenue = fields.Monetary()
    result = fields.Monetary()
    invested_capital = fields.Monetary()
    roi = fields.Float(string="ROI")
