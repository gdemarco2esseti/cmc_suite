import logging

_logger = logging.getLogger("cmc_mrp_post_migrate_1_2_0")


def migrate(cr, version):
    # why: cmc_bridge_source is new; without it a line the bridge classified
    # before the upgrade would keep its MOD values forever, even after it
    # stops being manufacturing labor. Only lines carrying exactly the bridge's values are
    # tagged: anything else was changed by a rule or by hand. A line whose
    # identical values came from a rule is tagged too; on withdrawal the rule
    # refills them in the same pass.
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
           AND l.category = 'manufacturing_order'
           -- labour only (hours, or no unit), never a consumption line we adopted
           AND l.cmc_mrp_move_id IS NULL
           AND (l.product_uom_id IS NULL OR l.product_uom_id = (
                SELECT res_id FROM ir_model_data WHERE module = 'uom' AND name = 'product_uom_hour'))
        """,
        ("mrp_labour:cmc_behavior,cmc_cost_element_id,cmc_directness",),
    )
    _logger.info("Tagged %s lines classified by the mrp_labour bridge.", cr.rowcount)
