from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_tdabc_activity_id = fields.Many2one(
        "cmc.tdabc.activity", string="TDABC Activity", index=True,
        help="Activity this actual-hours line contributes to (timesheet scenario).",
    )

    def _cmc_tdabc_domain(self, date_from, date_to, company):
        domain = [("cmc_tdabc_activity_id", "!=", False)]
        if company:
            domain.append(("company_id", "in", [False] + company.ids))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        return domain

    def _cmc_tdabc_hours(self, date_from=False, date_to=False, company=False):
        """{activity_id: actual minutes} from tagged analytic lines. unit_amount
        is in hours (timesheet convention) -> *60 to minutes."""
        groups = self._read_group(
            self._cmc_tdabc_domain(date_from, date_to, company),
            groupby=["cmc_tdabc_activity_id"], aggregates=["unit_amount:sum"],
        )
        return {act.id: (hours or 0.0) * 60.0 for act, hours in groups}
