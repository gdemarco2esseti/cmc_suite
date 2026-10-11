from odoo import fields, models, tools
from odoo.tools import SQL


class CmcEvmCurveSeries(models.Model):
    _name = "cmc.evm.curve.series"
    _description = "EVM S-curve series"
    _auto = False
    _order = "run_id, month, series"
    # why: the ORM flushes these columns before reading the view, so a just-computed run is seen
    _depends = {
        "cmc.evm.curve": ["run_id", "date", "is_forecast", "pv_cumulative", "ev_cumulative", "ac_cumulative"],
        "cmc.evm.run": ["project_id", "company_id"],
    }

    run_id = fields.Many2one("cmc.evm.run", readonly=True)
    project_id = fields.Many2one("project.project", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    month = fields.Date(readonly=True)
    series = fields.Selection(
        [("pv", "Planned Value (PV)"), ("ev", "Earned Value (EV)"), ("ac", "Actual Cost (AC)")], readonly=True,
    )
    # why: values are cumulative, so several runs/months must never be summed. In forecast months EV/AC repeat the
    # status-date figure (DL 2026-10-11): the graph fills a missing point with 0, which would show a fake collapse
    value = fields.Monetary(currency_field="currency_id", aggregator="max", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        # why: id = curve id x 3 + series offset, stable and unique without row_number()
        self.env.cr.execute(SQL(
            """
            CREATE OR REPLACE VIEW %s AS
            SELECT c.id * 3 + s.pos AS id, c.run_id, r.project_id, r.company_id, co.currency_id,
                   c.date AS month, s.series, s.value
              FROM cmc_evm_curve c
              JOIN cmc_evm_run r ON r.id = c.run_id
              JOIN res_company co ON co.id = r.company_id
              LEFT JOIN LATERAL (
                    SELECT p.ev_cumulative, p.ac_cumulative
                      FROM cmc_evm_curve p
                     WHERE p.run_id = c.run_id AND NOT COALESCE(p.is_forecast, FALSE) AND p.date <= c.date
                  ORDER BY p.date DESC
                     LIMIT 1
              ) AS last ON COALESCE(c.is_forecast, FALSE)
              CROSS JOIN LATERAL (VALUES
                    (0, 'pv', c.pv_cumulative),
                    (1, 'ev', CASE WHEN COALESCE(c.is_forecast, FALSE) THEN last.ev_cumulative ELSE c.ev_cumulative END),
                    (2, 'ac', CASE WHEN COALESCE(c.is_forecast, FALSE) THEN last.ac_cumulative ELSE c.ac_cumulative END)
              ) AS s(pos, series, value)
             WHERE s.series = 'pv' OR s.value IS NOT NULL
            """,
            SQL.identifier(self._table),
        ))
