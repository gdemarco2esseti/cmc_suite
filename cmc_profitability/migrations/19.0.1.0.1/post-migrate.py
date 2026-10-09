import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # CB-4: chargeback postings become relocation postings
    cr.execute("UPDATE account_analytic_line SET cmc_is_relocation = TRUE WHERE cmc_transfer_charge_id IS NOT NULL")
    _logger.info("cmc_profitability: flagged %d existing chargeback line(s)", cr.rowcount)
