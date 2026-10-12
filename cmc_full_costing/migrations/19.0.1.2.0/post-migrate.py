import logging

_logger = logging.getLogger("cmc_full_costing_post_migrate_1_2_0")


def migrate(cr, version):
    # why: before AD-001 "Reset to Draft" only moved the state, so a draft run
    # could still list results computed on inputs changed since. A draft run
    # holds no results now; quota and config rows follow by ON DELETE CASCADE.
    cr.execute(
        """
        DELETE FROM cmc_costing_result r
         USING cmc_costing_run run
         WHERE run.id = r.run_id AND run.state = 'draft'
        """
    )
    _logger.info("Removed %s stale results of draft costing runs.", cr.rowcount)
