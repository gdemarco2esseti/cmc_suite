from odoo import fields, models, tools
from odoo.tools import SQL

from .cmc_utils import month_end


class CmcCenterCostReport(models.Model):
    _name = "cmc.center.cost.report"
    _description = "Cost by Center and Roll-up"
    _auto = False
    _order = "date desc"
    _rec_name = "center_id"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Booked on", readonly=True)
    rollup_center_id = fields.Many2one("account.analytic.account", string="Rolled up to", readonly=True)
    depth = fields.Integer(readonly=True, help="0 = the center itself, 1 = its parent...")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", readonly=True)
    controllable = fields.Boolean(readonly=True)
    date = fields.Date(string="Month", readonly=True)
    cost = fields.Monetary(readonly=True, aggregator="sum")

    def _query(self):
        # one aggregated branch per ROOT plan column: the center sits in the column of its plan
        plans = self.env["account.analytic.plan"].sudo().search([("parent_id", "=", False)])
        booked = SQL(" UNION ALL ").join(
            SQL(
                """SELECT l.company_id, l.%(col)s AS center_id, l.cmc_cost_element_id AS element_id,
                          date_trunc('month', l.date)::date AS date, -SUM(l.amount) AS cost
                     FROM account_analytic_line l
                    WHERE l.cmc_excluded IS NOT TRUE AND l.%(col)s IS NOT NULL
                    GROUP BY 1, 2, 3, 4""",
                col=SQL.identifier(plan._column_name()),
            )
            for plan in plans
        )
        return SQL(
            """WITH RECURSIVE booked AS (%(booked)s),
                tree(center_id, rollup_center_id, depth) AS (
                    SELECT center_id, center_id, 0 FROM (SELECT DISTINCT center_id FROM booked) c
                    UNION ALL
                    SELECT t.center_id, a.cmc_parent_id, t.depth + 1
                      FROM tree t
                      JOIN account_analytic_account a ON a.id = t.rollup_center_id
                     WHERE a.cmc_parent_id IS NOT NULL
                )
                SELECT row_number() OVER (ORDER BY b.date, b.center_id, t.rollup_center_id, b.element_id, b.company_id) AS id,
                       b.company_id, b.center_id, t.rollup_center_id, t.depth, b.element_id,
                       COALESCE(e.cmc_controllable, false) AS controllable, b.date, b.cost
                  FROM booked b
                  JOIN tree t ON t.center_id = b.center_id
             LEFT JOIN cmc_cost_element e ON e.id = b.element_id""",
            booked=booked,
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(SQL("CREATE VIEW %s AS (%s)", SQL.identifier(self._table), self._query()))

    def action_open_lines(self):
        self.ensure_one()
        return self.center_id.action_cmc_drilldown(self.date, month_end(self.date))


class AccountAnalyticPlan(models.Model):
    _inherit = "account.analytic.plan"

    def _sync_plan_column(self, model):
        res = super()._sync_plan_column(model)
        if model == "account.analytic.line":
            self.env["cmc.center.cost.report"].init()
        return res

    def unlink(self):
        res = super().unlink()
        # why: dropping a plan drops its column with CASCADE, which silently drops the view
        self.env["cmc.center.cost.report"].init()
        return res
