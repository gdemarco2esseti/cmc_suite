import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # CB-4: lines posted by a run engine become relocation postings (the flag did not exist)
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'account_analytic_line' AND column_name = 'cmc_transfer_charge_id'")
    condition = "cmc_allocation_id IS NOT NULL" + (" OR cmc_transfer_charge_id IS NOT NULL" if cr.rowcount else "")
    cr.execute("UPDATE account_analytic_line SET cmc_is_relocation = TRUE WHERE " + condition)  # noqa: S608 - constant text
    _logger.info("cmc_base: flagged %d existing relocation line(s)", cr.rowcount)
