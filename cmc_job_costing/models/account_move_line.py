from odoo import api, models
from odoo.fields import Domain
from odoo.tools import SQL

from odoo.addons.cmc_account.models.account_move_line import REVENUE_TYPES


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    @api.model
    def _cmc_revenue_by_analytic(self, company, date_from, date_to, analytic_ids):
        """{analytic account id: revenue} of the posted income items, each weighted by the percentage
        its analytic distribution gives to the account (credit notes net, AD-111)."""
        if not analytic_ids:
            return {}
        scope = self._cmc_reader_scope(company)
        domain = (
            self._cmc_posted_domain(company, date_from, date_to)
            & Domain("account_id.account_type", "in", REVENUE_TYPES)
            & Domain("analytic_distribution", "in", list(analytic_ids))
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
            ids=[str(i) for i in analytic_ids],
        ))
        return {account_id: float(amount or 0.0) for account_id, amount in scope.env.cr.fetchall()}
