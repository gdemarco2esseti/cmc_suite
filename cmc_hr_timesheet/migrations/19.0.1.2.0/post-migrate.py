import logging

_logger = logging.getLogger("cmc_hr_timesheet_post_migrate_1_2_0")


def migrate(cr, version):
    # why: cmc_bridge_source is new; without it a line the bridge classified
    # before the upgrade would keep its MOD values forever, even after it
    # stops being direct labor. Only lines carrying exactly the bridge's values are
    # tagged: anything else was changed by a rule or by hand.
    cr.execute(
        """
        UPDATE account_analytic_line l
           SET cmc_bridge_source = %s
          FROM ir_model_data d
         WHERE d.module = 'cmc_base' AND d.name = 'cmc_element_mod'
           AND l.cmc_cost_element_id = d.res_id
           AND l.cmc_behavior = 'variable' AND l.cmc_directness = 'direct'
           AND l.cmc_bridge_source IS NULL
           AND l.cmc_classified_manually IS NOT TRUE
           AND l.cmc_excluded IS NOT TRUE
           AND l.project_id IS NOT NULL AND l.employee_id IS NOT NULL
        """,
        ("timesheet_mod",),
    )
    _logger.info("Tagged %s lines classified by the timesheet_mod bridge.", cr.rowcount)
