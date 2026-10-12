import json
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import sql

_logger = logging.getLogger(__name__)

OLD_COLUMNS = (
    "cmc_classified_manually",
    "cmc_behavior",
    "cmc_directness",
    "cmc_cost_element_id",
)


def _migrate_analytic_overrides(env):
    # the old flag on the journal item was copied to every sibling analytic line:
    # the lines that are themselves manual are what the new model keeps, keyed by
    # their own center (siblings manual on the line only included).
    env.cr.execute(
        "SELECT al.id FROM account_analytic_line al "
        "WHERE al.cmc_classified_manually AND al.move_line_id IS NOT NULL"
    )
    lines = env["account.analytic.line"].browse([row[0] for row in env.cr.fetchall()])
    Manual = env["cmc.manual.classification"]
    seen = {
        (rec.move_line_id.id, rec.center_id.id)
        for rec in Manual.search([("move_line_id", "in", lines.move_line_id.ids)])
    }
    to_create = []
    discarded = []
    for line in lines:
        center = line.cmc_center_id
        key = (line.move_line_id.id, center.id)
        if not center:
            discarded.append(line.id)
            continue
        if key in seen:
            continue
        seen.add(key)
        to_create.append({
            "move_line_id": key[0],
            "center_id": key[1],
            "cmc_behavior": line.cmc_behavior or False,
            "cmc_directness": line.cmc_directness or False,
            "cmc_cost_element_id": line.cmc_cost_element_id.id,
        })
    Manual.create(to_create)
    if discarded:
        _logger.warning(
            "cmc_account: %d manual classification(s) not migrated, the analytic "
            "line has no cost center: ids %s", len(discarded), discarded,
        )


def _migrate_draft_overrides(env):
    # why: button_draft deletes the analytic lines, so the override of an entry
    # in draft exists ONLY on the journal item columns about to be dropped.
    cr = env.cr
    cr.execute(
        "SELECT aml.id, aml.analytic_distribution, aml.cmc_behavior, aml.cmc_directness, aml.cmc_cost_element_id "
        "FROM account_move_line aml "
        "WHERE aml.cmc_classified_manually IS TRUE "
        "AND NOT EXISTS (SELECT 1 FROM account_analytic_line al WHERE al.move_line_id = aml.id) "
        "ORDER BY aml.id"
    )
    rows = cr.fetchall()
    keys = {}
    no_distribution = []
    for move_line_id, distribution, *_values in rows:
        if isinstance(distribution, str):
            distribution = json.loads(distribution)
        ids = {int(i) for key in (distribution or {}) for i in key.split(",") if i}
        if ids:
            keys[move_line_id] = ids
        else:
            no_distribution.append(move_line_id)
    all_ids = set().union(*keys.values()) if keys else set()
    # same definition of cost center as cmc_center_id: a typed center or a period account
    accounts = env["account.analytic.account"].browse(all_ids).exists()
    centers = set(accounts.filtered(lambda a: a.cmc_center_type or a.cmc_is_period).ids)
    existing = {
        (rec.move_line_id.id, rec.center_id.id)
        for rec in env["cmc.manual.classification"].search([("move_line_id", "in", list(keys))])
    }
    to_create = []
    for move_line_id, distribution, behavior, directness, element_id in rows:
        # ambiguous key (no center in it): every account of the key
        chosen = (keys.get(move_line_id, set()) & centers) or (keys.get(move_line_id, set()) & set(accounts.ids))
        for center_id in sorted(chosen):
            if (move_line_id, center_id) in existing:
                continue
            to_create.append({
                "move_line_id": move_line_id,
                "center_id": center_id,
                "cmc_behavior": behavior or False,
                "cmc_directness": directness or False,
                "cmc_cost_element_id": element_id or False,
            })
    env["cmc.manual.classification"].create(to_create)
    _logger.info(
        "cmc_account: %d manual override(s) of journal items without analytic lines carried to cmc.manual.classification",
        len(to_create),
    )
    if no_distribution:
        _logger.warning(
            "cmc_account: %d manual journal item(s) without analytic distribution, "
            "the override is lost: ids %s", len(no_distribution), no_distribution,
        )


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    if sql.column_exists(cr, "account_analytic_line", "cmc_classified_manually"):
        _migrate_analytic_overrides(env)
    if not sql.column_exists(cr, "account_move_line", "cmc_classified_manually"):
        return  # databases that never had the journal item columns
    _migrate_draft_overrides(env)
    for column in OLD_COLUMNS:
        cr.execute(f'ALTER TABLE account_move_line DROP COLUMN IF EXISTS "{column}"')  # noqa: S608 - constant names
