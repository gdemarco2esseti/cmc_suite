from odoo.tools import sql


def migrate(cr, version):
    if not sql.table_exists(cr, "cmc_closing_run"):
        return
    # why: date columns become NOT NULL; fill undated runs with the span they
    # already aggregated (the company's lines plus company-less ones, as
    # _cmc_cost_groups does) so stored figures hold.
    cr.execute(
        """
        UPDATE cmc_closing_run r
           SET date_from = COALESCE(r.date_from, l.dmin, r.create_date::date),
               date_to = COALESCE(r.date_to, l.dmax, r.create_date::date)
          FROM (SELECT r2.id, MIN(a.date) AS dmin, MAX(a.date) AS dmax
                  FROM cmc_closing_run r2
             LEFT JOIN account_analytic_line a
                    ON a.company_id = r2.company_id OR a.company_id IS NULL
                 WHERE r2.date_from IS NULL OR r2.date_to IS NULL
              GROUP BY r2.id) l
         WHERE l.id = r.id
        """
    )
