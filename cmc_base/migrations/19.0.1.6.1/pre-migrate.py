def migrate(cr, version):
    # CB-4 RN-001: rows linked to a run satisfy the new CHECK (link => flag) before it is created
    cr.execute("ALTER TABLE account_analytic_line ADD COLUMN IF NOT EXISTS cmc_is_relocation boolean")
    cr.execute("UPDATE account_analytic_line SET cmc_is_relocation = TRUE WHERE cmc_allocation_id IS NOT NULL AND cmc_is_relocation IS NOT TRUE")
