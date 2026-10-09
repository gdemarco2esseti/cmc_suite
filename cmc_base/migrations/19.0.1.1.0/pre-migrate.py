from odoo.tools import sql

_NO_COSTINGS = "SELECT NULL::int AS closing_run_id, NULL::date AS dmin, NULL::date AS dmax WHERE FALSE"
_COSTINGS = """
    SELECT closing_run_id, MIN(date_from) AS dmin, MAX(date_to) AS dmax
      FROM cmc_costing_run WHERE closing_run_id IS NOT NULL GROUP BY closing_run_id
"""


def migrate(cr, version):
    if not sql.table_exists(cr, "cmc_closing_run"):
        return
    costings = _COSTINGS if sql.table_exists(cr, "cmc_costing_run") else _NO_COSTINGS
    # why: date columns become NOT NULL; fill undated runs with the span they
    # already aggregated (the company's lines plus company-less ones, as
    # _cmc_cost_groups does), widened to the linked costings so those stay
    # inside the period and stored figures hold. LEAST/GREATEST skip NULLs;
    # a half-dated run is never inverted.
    cr.execute(
        f"""
        WITH span AS (
            SELECT company_id, MIN(date) AS dmin, MAX(date) AS dmax
              FROM account_analytic_line GROUP BY company_id
        ), costing AS ({costings}),
        calc AS (
            SELECT r.id,
                   LEAST(s.dmin, n.dmin, k.dmin) AS dmin,
                   GREATEST(s.dmax, n.dmax, k.dmax) AS dmax
              FROM cmc_closing_run r
         LEFT JOIN span s ON s.company_id = r.company_id
         LEFT JOIN span n ON n.company_id IS NULL
         LEFT JOIN costing k ON k.closing_run_id = r.id
             WHERE r.date_from IS NULL OR r.date_to IS NULL
        )
        UPDATE cmc_closing_run r
           SET date_from = COALESCE(r.date_from, LEAST(c.dmin, r.date_to), r.create_date::date),
               date_to = COALESCE(r.date_to, GREATEST(c.dmax, r.date_from), r.create_date::date)
          FROM calc c
         WHERE c.id = r.id
        """  # noqa: S608 - static fragments only, no user input
    )
