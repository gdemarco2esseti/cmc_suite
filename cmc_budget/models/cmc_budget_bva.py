from odoo import fields, models
from odoo.tools import SQL


class CmcBudgetBva(models.Model):
    _name = "cmc.budget.bva"
    _description = "Budget vs Actual"
    _auto = False
    _order = "date desc, center_id"
    _depends = {
        "cmc.budget.line": ["version_id", "date", "kind", "center_id", "element_id", "behavior", "business_line_id", "amount_company"],
        "cmc.plan.version": ["company_id", "version_type", "date_from", "date_to", "alert_threshold"],
        "account.analytic.line": ["company_id", "account_id", "date", "amount", "cmc_cost_element_id", "cmc_behavior", "cmc_excluded"],
        "account.analytic.account": ["cmc_center_type"],
        "account.move.line": ["company_id", "account_id", "date", "balance", "parent_state"],
        "account.account": ["account_type"],
    }

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    version_id = fields.Many2one("cmc.plan.version", readonly=True)
    version_type = fields.Selection(selection=lambda self: self.env["cmc.plan.version"]._fields["version_type"].selection, readonly=True)
    date = fields.Date(string="Month", readonly=True)
    kind = fields.Selection([("cost", "Cost"), ("revenue", "Revenue")], readonly=True)
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", readonly=True)
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", readonly=True)
    behavior = fields.Selection([("fixed", "Fixed"), ("variable", "Variable"), ("semi_variable", "Semi-variable")], readonly=True)
    business_line_id = fields.Many2one("account.analytic.account", string="Business Line", readonly=True)
    plan_amount = fields.Monetary(readonly=True, aggregator="sum")
    actual_amount = fields.Monetary(readonly=True, aggregator="sum")
    variance = fields.Monetary(readonly=True, aggregator="sum", help="Actual - plan: positive = favourable.")
    prior_actual_amount = fields.Monetary(string="Prior Year Actual", readonly=True, aggregator="sum")
    is_alert = fields.Boolean(string="Alert", readonly=True, aggregator="bool_or")

    @property
    def _table_query(self):
        # natural sign (cost < 0, revenue > 0); revenue is compared without center element or behavior
        return SQL(
            """
            WITH actual AS (
                SELECT l.company_id, date_trunc('month', l.date)::date AS date, 'cost'::varchar AS kind,
                       l.account_id AS center_id, l.cmc_cost_element_id AS element_id,
                       COALESCE(l.cmc_behavior, 'fixed')::varchar AS behavior, SUM(l.amount) AS amount
                  FROM account_analytic_line l
                  JOIN account_analytic_account c ON c.id = l.account_id AND c.cmc_center_type IS NOT NULL
                 WHERE l.cmc_excluded IS NOT TRUE
              GROUP BY 1, 2, 4, 5, 6
                 UNION ALL
                SELECT m.company_id, date_trunc('month', m.date)::date, 'revenue'::varchar,
                       NULL::int, NULL::int, NULL::varchar, -SUM(m.balance)
                  FROM account_move_line m
                  JOIN account_account a ON a.id = m.account_id
                 WHERE m.parent_state = 'posted' AND a.account_type IN ('income', 'income_other')
              GROUP BY 1, 2
            ), parts AS (
                SELECT l.version_id, l.date, l.kind, l.center_id,
                       CASE WHEN l.kind = 'cost' THEN l.element_id END AS element_id,
                       CASE WHEN l.kind = 'cost' THEN l.behavior END AS behavior,
                       l.business_line_id, l.amount_company AS plan_amount, 0.0 AS actual_amount, 0.0 AS prior_actual_amount
                  FROM cmc_budget_line l
                 UNION ALL
                SELECT v.id, a.date, a.kind, a.center_id, a.element_id, a.behavior, NULL::int, 0.0, a.amount, 0.0
                  FROM actual a
                  JOIN cmc_plan_version v ON v.company_id = a.company_id AND v.version_type IN ('budget', 'forecast')
                   AND a.date BETWEEN date_trunc('month', v.date_from)::date AND v.date_to
                 UNION ALL
                SELECT v.id, (a.date + INTERVAL '12 months')::date, a.kind, a.center_id, a.element_id, a.behavior, NULL::int,
                       0.0, 0.0, a.amount
                  FROM actual a
                  JOIN cmc_plan_version v ON v.company_id = a.company_id AND v.version_type IN ('budget', 'forecast')
                   AND (a.date + INTERVAL '12 months')::date BETWEEN date_trunc('month', v.date_from)::date AND v.date_to
            ), grouped AS (
                SELECT p.version_id, v.company_id, v.version_type, v.alert_threshold, p.date, p.kind, p.center_id, p.element_id,
                       p.behavior, p.business_line_id, SUM(p.plan_amount) AS plan_amount, SUM(p.actual_amount) AS actual_amount,
                       SUM(p.prior_actual_amount) AS prior_actual_amount
                  FROM parts p
                  JOIN cmc_plan_version v ON v.id = p.version_id
              GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
            )
            SELECT g.version_id::bigint * 1000000
                   + ROW_NUMBER() OVER (PARTITION BY g.version_id
                                        ORDER BY g.date, g.kind, g.center_id, g.element_id, g.behavior, g.business_line_id) AS id,
                   g.company_id, g.version_id, g.version_type, g.date, g.kind, g.center_id, g.element_id, g.behavior,
                   g.business_line_id, g.plan_amount, g.actual_amount, g.actual_amount - g.plan_amount AS variance,
                   g.prior_actual_amount,
                   (SUM(g.plan_amount) OVER w <> 0
                    AND ABS(SUM(g.actual_amount) OVER w - SUM(g.plan_amount) OVER w) / ABS(SUM(g.plan_amount) OVER w) * 100
                        > g.alert_threshold) AS is_alert
              FROM grouped g
            WINDOW w AS (PARTITION BY g.version_id, g.date, g.kind, g.center_id, g.element_id)
            """
        )
