from collections import defaultdict

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

    def _cmc_activity_pool_groups(self, date_from=False, date_to=False, company=False):
        """[(activity, center, cost element, behavior, pool cost (net, may be negative))] aggregated
        directly on the tag column. The activity tag is a single column on the line
        (unlike a cost center, which can live on any analytic plan column)."""
        domain = [("cmc_activity_id", "!=", False), ("cmc_excluded", "=", False)]
        if company:
            domain.append(("company_id", "in", [False] + company.ids))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        # why sudo: a certified pool must not depend on who presses Compute
        # (timesheet rules hide other users' lines); the company domain is the scope
        lines = self.sudo() if company else self
        return lines._cmc_cost_read_group(domain, groupby=["cmc_activity_id", "account_id", "cmc_cost_element_id", "cmc_behavior"])

    def _cmc_activity_pools(self, date_from=False, date_to=False, company=False):
        """{activity_id: pool cost (net, may be negative)}."""
        pools = defaultdict(float)
        for activity, _center, _element, _behavior, cost in self._cmc_activity_pool_groups(date_from, date_to, company):
            pools[activity.id] += cost
        return dict(pools)
