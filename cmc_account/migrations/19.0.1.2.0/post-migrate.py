from odoo import SUPERUSER_ID, api

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
    # the manual classification used to sit on the journal item and was copied
    # to every sibling analytic line: keep it only on the lines that were
    # themselves manual, keyed by their own center.
    cr.execute(
        "SELECT al.id FROM account_analytic_line al "
        "JOIN account_move_line aml ON aml.id = al.move_line_id "
        "WHERE aml.cmc_classified_manually AND al.cmc_classified_manually"
    )
    lines = env["account.analytic.line"].browse([row[0] for row in cr.fetchall()])
    Manual = env["cmc.manual.classification"]
    seen = {
        (rec.move_line_id.id, rec.center_id.id)
        for rec in Manual.search([("move_line_id", "in", lines.move_line_id.ids)])
    }
    to_create = []
    for line in lines:
        center = line.cmc_center_id
        key = (line.move_line_id.id, center.id)
        if not center or key in seen:
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
    for column in OLD_COLUMNS:
        cr.execute(f'ALTER TABLE account_move_line DROP COLUMN IF EXISTS "{column}"')  # noqa: S608 - constant names
