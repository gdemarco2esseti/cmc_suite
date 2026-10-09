#!/usr/bin/env python
"""Seed a COMPLETE Fase-1 demo into cmc_test to exercise the cmc_base UI."""
import datetime
import os
import sys

sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/odoo")
sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19")

import odoo
from odoo import api
from odoo.tools import config

config.parse_config(
    [
        "-c", "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/config/odoo.conf",
        "-d", "cmc_test",
        "--addons-path=" + os.environ.get("CMC_SEED_ADDONS", "odoo/addons,enterprise,ref18.2,custom"),
    ]
)

# CMC_SEED_DB / CMC_SEED_ADDONS: run the seed on a scratch db or another checkout
DB = os.environ.get("CMC_SEED_DB", "cmc_test")
JAN = datetime.date(2026, 1, 15)
registry = odoo.modules.registry.Registry(DB)
with registry.cursor() as cr:
    env = api.Environment(cr, odoo.SUPERUSER_ID, {})

    # admin login + CMC manager group
    admin = env.ref("base.user_admin")
    admin.password = "admin"
    admin.group_ids = [(4, env.ref("cmc_base.group_cmc_manager").id)]

    plan = env["account.analytic.plan"].create({"name": "Stabilimento"})
    driver = env["cmc.cost.driver"].create({"name": "Ore servizio", "code": "ORE"})

    el_mod = env["cmc.cost.element"].create({"name": "Manodopera", "code": "MOD"})
    el_mat = env["cmc.cost.element"].create({"name": "Materie", "code": "MAT"})
    el_amm = env["cmc.cost.element"].create({"name": "Ammortamenti", "code": "AMM"})
    el_fig = env["cmc.cost.element"].create(
        {"name": "Interessi di computo", "code": "INT", "cmc_kind": "figurative"}
    )

    def center(name, ctype=False, period=False):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": plan.id,
             "cmc_center_type": ctype, "cmc_is_period": period}
        )

    manut = center("Manutenzione", "auxiliary")
    z = center("Montaggio Z", "final")
    y = center("Montaggio Y", "final")
    comm = center("Commerciale", period=True)

    def cost(c, amount, element, behavior="fixed"):
        env["account.analytic.line"].create(
            {"name": "Costo %s" % element.code, "account_id": c.id, "amount": -amount,
             "date": JAN, "cmc_behavior": behavior, "cmc_cost_element_id": element.id}
        )

    cost(manut, 3600, el_mod)        # auxiliary pool to reallocate
    cost(z, 2400, el_amm)            # fixed (capacity)
    cost(z, 600, el_mat, "variable")
    cost(y, 600, el_mat)
    cost(comm, 200, el_mod)          # period (commercial) cost

    Svc = env["cmc.center.service"]
    Svc.create({"provider_id": manut.id, "receiver_id": z.id,
                "driver_id": driver.id, "quantity": 60})
    Svc.create({"provider_id": manut.id, "receiver_id": y.id,
                "driver_id": driver.id, "quantity": 60})

    # closing run -> Z & Y absorb 1800 each; builds the cost-analysis matrix
    closing = env["cmc.closing.run"].create(
        {"name": "Chiusura Gennaio", "method": "direct",
         "date_from": datetime.date(2026, 1, 1), "date_to": datetime.date(2026, 1, 31)}
    )
    closing.action_compute()

    # allocation example (common energy cost split by hours)
    env["cmc.allocation"].create(
        {"name": "Energia comune", "source_account_id": z.id, "driver_id": driver.id,
         "amount": 1000,
         "line_ids": [(0, 0, {"target_account_id": z.id, "quantity": 60}),
                      (0, 0, {"target_account_id": y.id, "quantity": 40})]}
    )

    # capacity analysis on Z: practical 120, actual 80 -> idle 800
    env["cmc.capacity.analysis"].create(
        {"center_id": z.id, "date_from": datetime.date(2026, 1, 1),
         "date_to": datetime.date(2026, 1, 31), "driver_id": driver.id,
         "capacity_theoretical": 150, "capacity_practical": 120, "capacity_actual": 80}
    )

    # products + figurative + full-cost run (4 levels)
    p1 = env["product.product"].create({"name": "Prodotto A", "standard_price": 50})
    p2 = env["product.product"].create({"name": "Prodotto B", "standard_price": 80})
    env["cmc.figurative.cost"].create(
        {"name": "Interessi su capitale", "cost_element_id": el_fig.id,
         "method": "interest", "capital": 2000, "rate": 0.05,
         "cost_object_ref": "product.product,%s" % p1.id, "cmc_directness": "direct"}
    )
    costing = env["cmc.costing.run"].create(
        {"name": "Costo pieno Gennaio", "closing_run_id": closing.id, "base": "multiple",
         "date_from": datetime.date(2026, 1, 1), "date_to": datetime.date(2026, 1, 31),
         "product_ids": [(6, 0, (p1 | p2).ids)],
         "line_ids": [
             (0, 0, {"center_id": z.id, "product_id": p1.id, "driver_id": driver.id, "driver_qty": 60}),
             (0, 0, {"center_id": z.id, "product_id": p2.id, "driver_id": driver.id, "driver_qty": 40}),
             (0, 0, {"center_id": y.id, "product_id": p1.id, "driver_id": driver.id, "driver_qty": 30}),
             (0, 0, {"center_id": y.id, "product_id": p2.id, "driver_id": driver.id, "driver_qty": 30}),
         ]}
    )
    costing.action_compute()

    cr.commit()
    print("SEED OK: closing=%s costing=%s p1=%s" % (closing.id, costing.id, p1.id))
