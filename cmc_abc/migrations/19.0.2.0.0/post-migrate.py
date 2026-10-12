from collections import defaultdict

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL

# v1 rows name a product; the v2 network speaks cost objects (AD-106)
TABLES = ("cmc_abc_line", "cmc_abc_result", "cmc_abc_activity_result")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    products = defaultdict(set)
    for table in TABLES:
        cr.execute(SQL(
            "SELECT DISTINCT company_id, product_id FROM %s WHERE product_id IS NOT NULL AND cost_object_id IS NULL",
            SQL.identifier(table),
        ))
        for company_id, product_id in cr.fetchall():
            products[company_id].add(product_id)
    for company_id, product_ids in products.items():
        env["cmc.cost.object"]._cmc_ensure_objects(
            env["product.product"].browse(sorted(product_ids)), company=env["res.company"].browse(company_id)
        )
    for table in TABLES:
        cr.execute(SQL(
            "UPDATE %(t)s SET cost_object_id = o.id FROM cmc_cost_object o"
            " WHERE o.object_type = 'product' AND o.res_model = 'product.product' AND o.res_id = %(t)s.product_id"
            " AND o.company_id = %(t)s.company_id AND %(t)s.cost_object_id IS NULL",
            t=SQL.identifier(table),
        ))
