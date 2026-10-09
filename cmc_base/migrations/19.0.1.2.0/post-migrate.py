import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, sql

_logger = logging.getLogger(__name__)

TABLE = "account_analytic_line"
MAX_LOGGED_IDS = 50
# columns that mark a line as born from a document or another flow: absent
# column = that flow is not installed, nothing to exclude
DOCUMENT_COLUMNS = ("move_line_id", "project_id", "employee_id", "cmc_mrp_move_id")


def migrate(cr, version):
    """AD-005: a cost is a NEGATIVE analytic amount. Flip the manual CMC cost
    entries that were stored positive; document-born lines already carry Odoo's
    natural sign."""
    has = lambda column: sql.column_exists(cr, TABLE, column)  # noqa: E731
    classified = [SQL("cmc_behavior IS NOT NULL"), SQL("cmc_cost_element_id IS NOT NULL")]
    if has("cmc_activity_id"):
        classified.append(SQL("cmc_activity_id IS NOT NULL"))
    conditions = [
        SQL("amount > 0"),
        SQL("(%s)", SQL(" OR ").join(classified)),
        SQL("cmc_excluded IS NOT TRUE"),
    ]
    conditions += [SQL("%s IS NULL", SQL.identifier(c)) for c in ("general_account_id", *DOCUMENT_COLUMNS) if has(c)]
    if has("category"):
        conditions.append(SQL("COALESCE(category, 'other') = 'other'"))
    cr.execute(SQL(
        "UPDATE %s SET amount = -amount WHERE %s RETURNING id",
        SQL.identifier(TABLE), SQL(" AND ").join(conditions),
    ))
    ids = sorted(row[0] for row in cr.fetchall())
    _logger.info(
        "cmc_base: flipped %d manual cost entries to the negative sign (ids: %s%s)",
        len(ids), ids[:MAX_LOGGED_IDS], " ..." if len(ids) > MAX_LOGGED_IDS else "",
    )
    if not ids:
        return
    # the raw UPDATE bypassed the ORM hooks: refresh the stored capacity figures
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env[TABLE.replace("_", ".")].browse(ids)
    lines._cmc_touch_capacity(lines._cmc_capacity_rows())
    env.flush_all()
