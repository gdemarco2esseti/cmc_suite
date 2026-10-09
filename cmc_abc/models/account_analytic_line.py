from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_activity_id = fields.Many2one(
        "cmc.abc.activity",
        string="ABC Activity",
        index=True,
        check_company=True,
        help="Activity whose resource cost pool this line contributes to.",
    )

    def _cmc_activity_pools(self, date_from=False, date_to=False, company=False):
        """{activity_id: pool cost (net, may be negative)} aggregated directly on the tag
        column. The activity tag is a single column on the line (unlike a cost
        center, which can live on any analytic plan column)."""
        domain = [("cmc_activity_id", "!=", False)]
        if company:
            domain.append(("company_id", "in", [False] + company.ids))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        groups = self._cmc_cost_read_group(domain, groupby=["cmc_activity_id"])
        return {activity.id: cost for activity, cost in groups}
