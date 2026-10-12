from odoo.tools.sql import column_exists


def migrate(cr, version):
    # CB-4 RN-001: rows linked to a chargeback satisfy the new CHECK (link => flag) before it is created
    cr.execute("ALTER TABLE account_analytic_line ADD COLUMN IF NOT EXISTS cmc_is_relocation boolean")
    # why: a db jumping from before the link column existed has nothing to flag yet
    if column_exists(cr, "account_analytic_line", "cmc_transfer_charge_id"):
        cr.execute("UPDATE account_analytic_line SET cmc_is_relocation = TRUE WHERE cmc_transfer_charge_id IS NOT NULL AND cmc_is_relocation IS NOT TRUE")
