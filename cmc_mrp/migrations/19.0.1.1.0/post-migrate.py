import logging

from odoo import SUPERUSER_ID, api
from odoo.addons.cmc_base.models.account_analytic_line import CAPACITY_DERIVED_FIELDS
from odoo.tools import SQL, sql

_logger = logging.getLogger(__name__)

# journal entries core booked for workcenter time (mrp_account stamps their item on the time)
LABOUR_ENTRIES = "SELECT aml.move_id FROM mrp_workcenter_productivity p JOIN account_move_line aml ON aml.id = p.account_move_line_id"


def migrate(cr, version):
    """The labour journal entry of a manufacturing order is the twin of the
    workcenter analytic line: exclude it (and its analytic lines) for the
    entries booked before the exclusion existed."""
    if not sql.column_exists(cr, "mrp_workcenter_productivity", "account_move_line_id"):
        return  # mrp_account is not there: no labour entries
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(SQL("UPDATE account_move_line SET cmc_excluded = TRUE WHERE move_id IN (%s)", SQL(LABOUR_ENTRIES)))
    sets = [SQL("cmc_excluded = TRUE"), SQL("cmc_behavior = NULL"), SQL("cmc_cost_element_id = NULL"), SQL("cmc_directness = 'indirect'")]
    if sql.column_exists(cr, "account_analytic_line", "cmc_classified_manually"):
        sets.append(SQL("cmc_classified_manually = FALSE"))
    cr.execute(SQL(
        "UPDATE account_analytic_line SET %s WHERE move_line_id IN (SELECT id FROM account_move_line WHERE move_id IN (%s))",
        SQL(", ").join(sets), SQL(LABOUR_ENTRIES),
    ))
    count = cr.rowcount
    _logger.info("cmc_mrp: %d historical labour twin analytic line(s) excluded from the cost base", count)
    if not count:
        return
    # the raw UPDATE bypassed the ORM hooks: drop stale caches and refresh ALL stored capacity figures
    env["account.analytic.line"].invalidate_model()
    rows = env["cmc.capacity.analysis"].search([])
    for fname in CAPACITY_DERIVED_FIELDS:
        env.add_to_compute(rows._fields[fname], rows)
    env.flush_all()
