import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD_COLUMNS = (
    "cmc_classified_manually",
    "cmc_behavior",
    "cmc_directness",
    "cmc_cost_element_id",
)


def migrate(cr, version):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'account_move_line' AND column_name = 'cmc_classified_manually'"
    )
    if not cr.fetchone():
        return  # fresh install or already migrated
    env = api.Environment(cr, SUPERUSER_ID, {})
    # the old flag on the journal item was copied to every sibling analytic line:
    # the lines that are themselves manual are what the new model keeps, keyed by
    # their own center (siblings manual on the line only included).
    cr.execute(
        "SELECT al.id FROM account_analytic_line al "
        "WHERE al.cmc_classified_manually AND al.move_line_id IS NOT NULL"
    )
    lines = env["account.analytic.line"].browse([row[0] for row in cr.fetchall()])
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
    for column in OLD_COLUMNS:
        cr.execute(f'ALTER TABLE account_move_line DROP COLUMN IF EXISTS "{column}"')  # noqa: S608 - constant names
