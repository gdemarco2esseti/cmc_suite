from odoo import api, models
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.tools import SQL

REVENUE_TYPES = ("income", "income_other")
EXPENSE_TYPES = ("expense", "expense_depreciation", "expense_direct_cost")


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
        domain = Domain([("parent_state", "=", "posted"), ("company_id", "=", company.id), ("date", "<=", date_to)])
        # no lower bound = from the beginning (job-to-date readers)
        return domain & Domain("date", ">=", date_from) if date_from else domain

    @api.model
    def _cmc_revenue_read_group(self, company, date_from, date_to, groupby=()):
        domain = self._cmc_posted_domain(company, date_from, date_to) & Domain(
            "account_id.account_type", "in", REVENUE_TYPES
        )
        rows = self._cmc_reader_scope(company)._read_group(domain, list(groupby), ["balance:sum"])
        return [(*row[:-1], -(row[-1] or 0.0)) for row in rows]

    @api.model
    def _cmc_revenue_by_analytic(self, company, date_from, date_to, analytic_accounts):
        """{analytic account id: revenue} of the posted income items, each weighted by the percentage
        its analytic distribution gives to the account (credit notes net, AD-111)."""
        scope = self._cmc_reader_scope(company)
        if not analytic_accounts:
            return {}
        domain = (
            self._cmc_posted_domain(company, date_from, date_to)
            & Domain("account_id.account_type", "in", REVENUE_TYPES)
            & Domain("analytic_distribution", "in", analytic_accounts.ids)
        )
        query = scope._search(domain)
        scope.env.flush_all()
        # why SQL: a JSON distribution cannot be grouped with its percentage by the ORM; keys can be "id1,id2"
        scope.env.cr.execute(SQL(
            "SELECT acc::int, -SUM(%(balance)s * kv.value::numeric / 100) FROM %(from)s,"
            " jsonb_each_text(%(dist)s) kv, unnest(string_to_array(kv.key, ',')) acc"
            " WHERE %(where)s AND acc = ANY(%(ids)s) GROUP BY acc",
            balance=scope._field_to_sql(scope._table, "balance", query),
            dist=scope._field_to_sql(scope._table, "analytic_distribution", query),
            **{"from": query.from_clause},
            where=query.where_clause,
            ids=[str(i) for i in analytic_accounts.ids],
        ))
        return {account_id: float(amount or 0.0) for account_id, amount in scope.env.cr.fetchall()}

    @api.model
    def _cmc_gl_balance_read_group(self, company, date_from, date_to, groupby=("account_id", "date:month")):
        domain = self._cmc_posted_domain(company, date_from, date_to)
        return self._cmc_reader_scope(company)._read_group(domain, list(groupby), ["balance:sum"])

    @api.model
    def _cmc_gl_balances_at(self, company, date_to, exclude_journals=None):
        scope = self._cmc_reader_scope(company)
        # why sudo: account_accountant reads account.fiscal.year here, which CMC users cannot read
        year_start = company.sudo().compute_fiscalyear_dates(date_to)["date_from"]
        base = [("parent_state", "=", "posted"), ("company_id", "=", company.id), ("date", "<=", date_to)]
        if exclude_journals:
            base.append(("journal_id", "not in", exclude_journals.ids))
        balances = {}
        for initial, domain in (
            (True, Domain(base)),
            (False, Domain(base) & Domain("date", ">=", year_start)),
        ):
            domain &= Domain("account_id.include_initial_balance", "=", initial)
            for account, balance in scope._read_group(domain, ["account_id"], ["balance:sum"]):
                balances[account] = balance or 0.0
        return balances
