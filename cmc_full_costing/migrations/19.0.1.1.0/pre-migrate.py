from odoo.tools import sql


def migrate(cr, version):
    # why: date columns become NOT NULL; an undated costing run reads the whole
    # period of its closing run (cmc_base has already given every closing a period)
    if not sql.table_exists(cr, "cmc_costing_run") or not sql.column_exists(cr, "cmc_costing_run", "date_from"):
        return
    cr.execute(
        """
        UPDATE cmc_costing_run r
           SET date_from = COALESCE(r.date_from, c.date_from),
               date_to = COALESCE(r.date_to, c.date_to)
          FROM cmc_closing_run c
         WHERE c.id = r.closing_run_id AND (r.date_from IS NULL OR r.date_to IS NULL)
        """
    )
