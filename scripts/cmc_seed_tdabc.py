#!/usr/bin/env python
"""Inject the Time-Driven ABC demo (warehouse example) into cmc_all.

Standalone + idempotent: reuses the existing demo products/center, skips if the
'TDABC Gennaio' run already exists. Use this to add TDABC to an already-seeded
cmc_all; a fresh reseed via cmc_seed_full.py already includes the same block.
"""
import datetime
import os
import sys

sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/odoo")
sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19")

import odoo
from odoo import api
from odoo.tools import config

# CMC_SEED_DB / CMC_SEED_ADDONS: run the seed on a scratch db or another checkout
DB = os.environ.get("CMC_SEED_DB", "cmc_all")
config.parse_config([
    "-c", "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/config/odoo.conf",
    "-d", DB,
    "--addons-path=" + os.environ.get("CMC_SEED_ADDONS", "odoo/addons,enterprise,custom"),
])

JAN15 = datetime.date(2026, 1, 15)

registry = odoo.modules.registry.Registry(DB)
with registry.cursor() as cr:
    env = api.Environment(cr, odoo.SUPERUSER_ID, {})

    if env["cmc.tdabc.run"].search_count([("name", "=", "TDABC Gennaio")]):
        cr.commit()
        print("TDABC SEED SKIP: already present")
        sys.exit(0)

    p1 = env["product.product"].search([("name", "=", "Prodotto A")], limit=1)
    p2 = env["product.product"].search([("name", "=", "Prodotto B")], limit=1)
    z = env["account.analytic.account"].search(
        [("name", "=", "Montaggio Z"), ("cmc_center_type", "=", "final")], limit=1)
    if not (p1 and p2 and z):
        print("TDABC SEED ABORT: base demo missing (run cmc_seed_full.py first)")
        sys.exit(1)

    wh = env["cmc.tdabc.resource"].create(
        {"name": "Magazzino", "cost": 80000,
         "theoretical_time": 211200, "util_factor": 75})  # practical 158400, rate 0.5
    oversize = env["cmc.tdabc.characteristic"].create(
        {"code": "OVERSIZE", "name": "Fuori misura", "ttype": "bool"})
    p2.product_tmpl_id.tdabc_char_value_ids = [(0, 0, {
        "characteristic_id": oversize.id, "value": 1.0})]
    t_pick = env["cmc.tdabc.activity"].create(
        {"name": "Prelievo (pick)", "code": "PICK", "resource_id": wh.id, "base_time": 15,
         "term_ids": [(0, 0, {"characteristic_id": oversize.id, "coefficient": 10})]})
    t_pack = env["cmc.tdabc.activity"].create(
        {"name": "Imballo speciale", "code": "PACK", "resource_id": wh.id, "base_time": 10})
    t_air = env["cmc.tdabc.activity"].create(
        {"name": "Spedizione aerea", "code": "AIR", "resource_id": wh.id, "base_time": 5})
    for act, hrs in ((t_pick, 1900), (t_pack, 320), (t_air, 80)):
        env["account.analytic.line"].create(
            {"name": "Ore %s" % act.code, "account_id": z.id, "date": JAN15,
             "unit_amount": hrs, "cmc_tdabc_activity_id": act.id})
    tdabc = env["cmc.tdabc.run"].create(
        {"name": "TDABC Gennaio",
         "line_ids": [
             (0, 0, {"activity_id": t_pick.id, "cost_object_type": "product",
                     "product_id": p1.id, "volume": 8000}),
             (0, 0, {"activity_id": t_pack.id, "cost_object_type": "product",
                     "product_id": p1.id, "volume": 2000}),
             (0, 0, {"activity_id": t_air.id, "cost_object_type": "product",
                     "product_id": p2.id, "volume": 1000}),
         ]})
    tdabc.action_compute()

    cr.commit()
    caps = tdabc.capacity_result_ids.filtered(lambda c: c.scenario == "manual")
    print("TDABC SEED OK: run=%s required=%s unused_cost=%s use%%=%.1f"
          % (tdabc.id, caps.required_time, caps.unused_cost, caps.capacity_use_pct))
