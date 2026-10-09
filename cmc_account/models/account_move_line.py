from odoo import api, models
from odoo.exceptions import AccessError
from odoo.fields import Domain

REVENUE_TYPES = ("income", "income_other")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    @api.model
    def _cmc_reader_scope(self, company):
        """Sudo env on the posted journal items of `company`, after the CMC gate."""
        if not self.env.su and not (
            self.env.user.has_group("cmc_base.group_cmc_user") and company in self.env.companies
        ):
            raise AccessError(self.env._("You are not allowed to read the general ledger of %s.", company.display_name))
        # why sudo: CMC users aggregate the GL without accounting rights, inside the gate above
        return self.sudo()

    @api.model
    def _cmc_posted_domain(self, company, date_from, date_to):
        return Domain([
            ("parent_state", "=", "posted"),
            ("company_id", "=", company.id),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ])

    @api.model
    def _cmc_revenue_read_group(self, company, date_from, date_to, groupby=()):
        domain = self._cmc_posted_domain(company, date_from, date_to) & Domain(
            "account_id.account_type", "in", REVENUE_TYPES
        )
        rows = self._cmc_reader_scope(company)._read_group(domain, list(groupby), ["balance:sum"])
        return [(*row[:-1], -(row[-1] or 0.0)) for row in rows]

    @api.model
    def _cmc_gl_balance_read_group(self, company, date_from, date_to, groupby=("account_id", "date:month")):
        domain = self._cmc_posted_domain(company, date_from, date_to)
        return self._cmc_reader_scope(company)._read_group(domain, list(groupby), ["balance:sum"])

    @api.model
    def _cmc_gl_balances_at(self, company, date_to):
        scope = self._cmc_reader_scope(company)
        # why sudo: account_accountant reads account.fiscal.year here, which CMC users cannot read
        year_start = company.sudo().compute_fiscalyear_dates(date_to)["date_from"]
        base = [("parent_state", "=", "posted"), ("company_id", "=", company.id), ("date", "<=", date_to)]
        balances = {}
        for initial, domain in (
            (True, Domain(base)),
            (False, Domain(base) & Domain("date", ">=", year_start)),
        ):
            domain &= Domain("account_id.include_initial_balance", "=", initial)
            for account, balance in scope._read_group(domain, ["account_id"], ["balance:sum"]):
                balances[account] = balance or 0.0
        return balances
