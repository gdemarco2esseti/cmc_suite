import logging

from odoo import SUPERUSER_ID, api
from odoo.addons.cmc_base.models.account_analytic_line import CAPACITY_DERIVED_FIELDS
from odoo.tools import SQL, sql

_logger = logging.getLogger(__name__)

TABLE = "account_analytic_line"
MAX_LOGGED_IDS = 50
STOCK_REL = "account_analytic_line_stock_move_rel"
# columns that mark a line as born from a document or another flow: absent
# column = that flow is not installed, nothing to exclude
DOCUMENT_COLUMNS = ("move_line_id", "project_id", "employee_id", "cmc_mrp_move_id")
# (table, extra condition): the runs that read the cost figures of a period
RUN_TABLES = (
    ("cmc_closing_run", None),
    ("cmc_costing_run", None),
    ("cmc_cvr_analysis", "r.mode = 'consuntivo'"),
    ("cmc_abc_run", None),
)


def _ids_log(ids):
    return "%s%s" % (ids[:MAX_LOGGED_IDS], " ..." if len(ids) > MAX_LOGGED_IDS else "")


def _stale_runs(cr, line_ids):
    """Concluded runs whose period contains one of the lines: computed on the old sign."""
    runs = []
    for table, extra in RUN_TABLES:
        if not sql.table_exists(cr, table) or not all(sql.column_exists(cr, table, c) for c in ("name", "date_from", "date_to", "company_id")):
            continue
        # a CVR analysis has no state: its actual-mode fixed cost is stored when computed
        done = SQL("AND r.state = 'done'") if sql.column_exists(cr, table, "state") else SQL()
        cr.execute(SQL(
            "SELECT r.id, r.name, r.company_id FROM %s r WHERE TRUE %s %s AND EXISTS ("
            "SELECT 1 FROM account_analytic_line l WHERE l.id = ANY(%s) AND l.company_id = r.company_id "
            "AND (r.date_from IS NULL OR l.date >= r.date_from) AND (r.date_to IS NULL OR l.date <= r.date_to)) ORDER BY r.id",
            SQL.identifier(table), done, SQL("AND %s", SQL(extra)) if extra else SQL(), list(line_ids),
        ))
        runs += [(table, *row) for row in cr.fetchall()]
    return runs


def migrate(cr, version):
    """AD-005: a cost is a NEGATIVE analytic amount. Flip the manual CMC cost
    entries that were stored positive; document-born lines already carry Odoo's
    natural sign."""
    has = lambda column: sql.column_exists(cr, TABLE, column)  # noqa: E731
    env = api.Environment(cr, SUPERUSER_ID, {})
    # the raw UPDATE below must see pending ORM writes
    env.flush_all()
    classified = [SQL("cmc_behavior IS NOT NULL"), SQL("cmc_cost_element_id IS NOT NULL")]
    if has("cmc_activity_id"):
        classified.append(SQL("cmc_activity_id IS NOT NULL"))
    conditions = [
        SQL("(%s)", SQL(" OR ").join(classified)),
        SQL("cmc_excluded IS NOT TRUE"),
    ]
    conditions += [SQL("%s IS NULL", SQL.identifier(c)) for c in ("general_account_id", *DOCUMENT_COLUMNS) if has(c)]
    if has("category"):
        conditions.append(SQL("COALESCE(category, 'other') = 'other'"))
    if sql.table_exists(cr, STOCK_REL):
        # stock_account books its valuation lines as category 'other', linked only through this M2M
        conditions.append(SQL("NOT EXISTS (SELECT 1 FROM %s WHERE %s = account_analytic_line.id)", SQL.identifier(STOCK_REL), SQL.identifier(STOCK_REL, "account_analytic_line_id")))
    by_rule = None
    if has("cmc_classified_manually"):
        # cmc_account marks as manual what a person classified; rule-born entries are not ours to flip.
        # Rules only set behavior/element, so a line carrying neither (activity tag only) is manual too.
        # Without cmc_account there are no rules: every classified line is manual.
        manual = SQL("(cmc_classified_manually IS TRUE OR (cmc_behavior IS NULL AND cmc_cost_element_id IS NULL))")
        by_rule = SQL(" AND ").join([*conditions, SQL("NOT %s", manual)])
        conditions.append(manual)

    where = SQL(" AND ").join(conditions)
    if by_rule is not None:
        cr.execute(SQL("SELECT id FROM %s WHERE amount > 0 AND %s ORDER BY id", SQL.identifier(TABLE), by_rule))
        left_alone = [row[0] for row in cr.fetchall()]
        if left_alone:
            _logger.warning(
                "cmc_base: %d positive classified entries without a document classified by a rule: "
                "under the new sign a positive amount reduces cost — verify by hand (ids: %s)",
                len(left_alone), _ids_log(left_alone),
            )
    cr.execute(SQL("SELECT id FROM %s WHERE amount < 0 AND %s ORDER BY id", SQL.identifier(TABLE), where))
    ambiguous = [row[0] for row in cr.fetchall()]
    if ambiguous:
        _logger.warning(
            "cmc_base: %d classified entries without a document already have a negative amount "
            "(old-convention reversals are indistinguishable from costs): verify by hand: "
            "under the new sign a negative amount is a cost (ids: %s)",
            len(ambiguous), _ids_log(ambiguous),
        )
    cr.execute(SQL(
        "UPDATE %s SET amount = -amount WHERE amount > 0 AND %s RETURNING id",
        SQL.identifier(TABLE), where,
    ))
    ids = sorted(row[0] for row in cr.fetchall())
    _logger.info("cmc_base: flipped %d manual cost entries to the negative sign (ids: %s)", len(ids), _ids_log(ids))
    if ids or ambiguous:
        for table, run_id, name, company_id in _stale_runs(cr, ids + ambiguous):
            _logger.warning(
                "cmc_base: %s %d (%s, company %s) concluded and computed on the old sign: reset to draft and recompute",
                table, run_id, name, company_id,
            )
    if not ids:
        return
    # the raw UPDATE bypassed the ORM hooks: drop stale caches and refresh ALL stored capacity figures
    env[TABLE.replace("_", ".")].invalidate_model(["amount"])
    rows = env["cmc.capacity.analysis"].search([])
    for fname in CAPACITY_DERIVED_FIELDS:
        env.add_to_compute(rows._fields[fname], rows)
    env.flush_all()
