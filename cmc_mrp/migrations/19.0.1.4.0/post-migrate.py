import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger("cmc_mrp_post_migrate_1_4_0")


def migrate(cr, version):
    # why: MP and workcenter lines booked before the stamp (AD-106) carry no cost object, so the finished product
    # misses its cost. Set-based: the live helper resolves the objects per company, one UPDATE stamps the lines.
    env = api.Environment(cr, SUPERUSER_ID, {})
    # why center required: an MO without CMC center is outside CMC product costing (RN-003)
    cr.execute(
        """
        SELECT l.id, mo.id
          FROM account_analytic_line l
          JOIN stock_move m ON m.id = l.cmc_mrp_move_id
          JOIN mrp_production mo ON mo.id = m.raw_material_production_id
         WHERE l.cmc_cost_object_id IS NULL AND mo.cmc_center_id IS NOT NULL
        UNION
        SELECT l.id, wo.production_id
          FROM account_analytic_line l
          JOIN mrp_workorder_wc_analytic_rel rel ON rel.account_analytic_line_id = l.id
          JOIN mrp_workorder wo ON wo.id = rel.mrp_workorder_id
          JOIN mrp_production mo ON mo.id = wo.production_id
         WHERE l.cmc_cost_object_id IS NULL AND mo.cmc_center_id IS NOT NULL
        """
    )
    line_mo = dict(cr.fetchall())
    if not line_mo:
        return
    productions = env["mrp.production"].browse(set(line_mo.values()))
    object_ids = productions._cmc_product_object_ids()
    cr.execute(
        """
        UPDATE account_analytic_line l
           SET cmc_cost_object_id = v.object_id
          FROM unnest(%s::int[], %s::int[]) AS v(line_id, object_id)
         WHERE l.id = v.line_id AND l.cmc_cost_object_id IS NULL
        """,
        (list(line_mo), [object_ids[mo_id] for mo_id in line_mo.values()]),
    )
    _logger.info("Stamped the finished-product cost object on %s manufacturing lines.", cr.rowcount)
    env.invalidate_all()
