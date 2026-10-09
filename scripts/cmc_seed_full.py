#!/usr/bin/env python
"""Seed a COMPLETE demo of the whole CMC suite (8 modules) into cmc_all.

Covers: cost taxonomy/centers, ribaltamento (closing), allocation, capacity,
full costing (4 levels) + figurative, direct costing + CVR/break-even, ABC,
joint costs, and accounting integration (mapping + classification rule).
Idempotent: skips if the demo centers already exist.
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

JAN1 = datetime.date(2026, 1, 1)
JAN15 = datetime.date(2026, 1, 15)
JAN31 = datetime.date(2026, 1, 31)

registry = odoo.modules.registry.Registry(DB)
with registry.cursor() as cr:
    env = api.Environment(cr, odoo.SUPERUSER_ID, {})

    # admin login + CMC manager group so the UI is browsable as admin/admin
    admin = env.ref("base.user_admin")
    admin.password = "admin"
    admin.group_ids = [(4, env.ref("cmc_base.group_cmc_manager").id)]

    if env["account.analytic.account"].search_count(
        [("name", "=", "Montaggio Z"), ("cmc_center_type", "=", "final")]
    ):
        cr.commit()
        print("SEED SKIP: demo already present")
        sys.exit(0)

    # root analytic plan (column account_id) — cost centers live here so the
    # required account_id on analytic lines is always satisfied
    plan = env["account.analytic.plan"].search(
        [("parent_id", "=", False)]
    ).filtered(lambda p: p._column_name() == "account_id")[:1]

    driver = env["cmc.cost.driver"].create({"name": "Ore servizio", "code": "ORE"})

    # standard cost-element taxonomy: reuse the shared MP/MOD from cmc_base
    el_mod = env.ref("cmc_base.cmc_element_mod")
    el_mat = env.ref("cmc_base.cmc_element_mp")
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

    def cost(c, amount, element, behavior="fixed", **kw):
        vals = {"name": "Costo %s" % element.code, "account_id": c.id,
                "amount": -amount, "date": JAN15, "cmc_behavior": behavior,
                "cmc_cost_element_id": element.id}
        vals.update(kw)
        return env["account.analytic.line"].create(vals)

    cost(manut, 3600, el_mod)            # auxiliary pool to reallocate
    cost(z, 2400, el_amm)                # fixed capacity cost
    cost(z, 600, el_mat, "variable")
    cost(y, 600, el_mat)
    cost(comm, 200, el_mod)              # period (commercial) cost

    Svc = env["cmc.center.service"]
    Svc.create({"provider_id": manut.id, "receiver_id": z.id,
                "driver_id": driver.id, "quantity": 60})
    Svc.create({"provider_id": manut.id, "receiver_id": y.id,
                "driver_id": driver.id, "quantity": 60})

    # --- ribaltamento (Z & Y absorb 1800 each) + cost-analysis matrix ---
    closing = env["cmc.closing.run"].create(
        {"name": "Chiusura Gennaio", "method": "direct",
         "date_from": JAN1, "date_to": JAN31}
    )
    closing.action_compute()

    # allocation example (common energy cost split by hours)
    env["cmc.allocation"].create(
        {"name": "Energia comune", "source_account_id": z.id, "driver_id": driver.id,
         "amount": 1000,
         "line_ids": [(0, 0, {"target_account_id": z.id, "quantity": 60}),
                      (0, 0, {"target_account_id": y.id, "quantity": 40})]}
    )

    # capacity: practical 120, actual 80 -> idle cost isolated
    env["cmc.capacity.analysis"].create(
        {"center_id": z.id, "date_from": JAN1, "date_to": JAN31,
         "driver_id": driver.id, "capacity_theoretical": 150,
         "capacity_practical": 120, "capacity_actual": 80}
    )

    # --- full costing (4 levels) + figurative ---
    p1 = env["product.product"].create({"name": "Prodotto A", "standard_price": 50})
    p2 = env["product.product"].create({"name": "Prodotto B", "standard_price": 80})
    env["cmc.figurative.cost"].create(
        {"name": "Interessi su capitale", "cost_element_id": el_fig.id,
         "method": "interest", "capital": 2000, "rate": 0.05,
         "cost_object_ref": "product.product,%s" % p1.id, "cmc_directness": "direct"}
    )
    costing = env["cmc.costing.run"].create(
        {"name": "Costo pieno Gennaio", "closing_run_id": closing.id,
         "date_from": JAN1, "date_to": JAN31,
         "base": "multiple", "product_ids": [(6, 0, (p1 | p2).ids)],
         "line_ids": [
             (0, 0, {"center_id": z.id, "product_id": p1.id, "driver_id": driver.id, "driver_qty": 60}),
             (0, 0, {"center_id": z.id, "product_id": p2.id, "driver_id": driver.id, "driver_qty": 40}),
             (0, 0, {"center_id": y.id, "product_id": p1.id, "driver_id": driver.id, "driver_qty": 30}),
             (0, 0, {"center_id": y.id, "product_id": p2.id, "driver_id": driver.id, "driver_qty": 30}),
         ]}
    )
    costing.action_compute()

    # --- Fase 2: CVR / break-even (single product) ---
    env["cmc.cvr.analysis"].create(
        {"name": "Break-even Prodotto A", "price_unit": 100, "variable_unit": 60,
         "fixed_cost": 20000, "volume": 700, "target_profit": 5000}
    )
    # direct costing (contribution margin by line)
    env["cmc.direct.costing"].create(
        {"name": "Direct Costing Gennaio", "common_fixed_cost": 5000,
         "line_ids": [
             (0, 0, {"name": "Prodotto A", "revenue": 70000, "variable_cost": 42000,
                     "specific_fixed_cost": 8000}),
             (0, 0, {"name": "Prodotto B", "revenue": 32000, "variable_cost": 20000,
                     "specific_fixed_cost": 6000}),
         ]}
    )

    # --- Fase 3: ABC ---
    act_setup = env["cmc.abc.activity"].create({"name": "Attrezzaggio", "code": "SET"})
    act_qc = env["cmc.abc.activity"].create({"name": "Collaudo", "code": "QC"})
    # resource costs tagged to activities (stage 1 pool)
    cost(z, 1500, el_mod, "variable", cmc_activity_id=act_setup.id)
    cost(z, 800, el_mod, "variable", cmc_activity_id=act_qc.id)
    abc = env["cmc.abc.run"].create(
        {"name": "ABC Gennaio",
         "line_ids": [
             (0, 0, {"activity_id": act_setup.id, "product_id": p1.id, "driver_qty": 12}),
             (0, 0, {"activity_id": act_setup.id, "product_id": p2.id, "driver_qty": 8}),
             (0, 0, {"activity_id": act_qc.id, "product_id": p1.id, "driver_qty": 30}),
             (0, 0, {"activity_id": act_qc.id, "product_id": p2.id, "driver_qty": 10}),
         ]}
    )
    abc.action_compute()

    # --- Fase 3: joint costs (raffineria: due congiunti + un sottoprodotto) ---
    jp1 = env["product.product"].create({"name": "Benzina", "standard_price": 0})
    jp2 = env["product.product"].create({"name": "Gasolio", "standard_price": 0})
    jbp = env["product.product"].create({"name": "Residuo (sottoprodotto)", "standard_price": 0})
    joint = env["cmc.joint.run"].create(
        {"name": "Ripartizione congiunti", "method": "sales_value", "joint_cost": 100000,
         "line_ids": [
             (0, 0, {"product_id": jp1.id, "quantity": 8000, "unit_sales_value": 12}),
             (0, 0, {"product_id": jp2.id, "quantity": 6000, "unit_sales_value": 10}),
             (0, 0, {"product_id": jbp.id, "quantity": 1000, "unit_sales_value": 2,
                     "is_byproduct": True}),
         ]}
    )
    joint.action_compute()

    # --- Time-Driven ABC (esempio magazzino) ---
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
    # actual hours per activity -> timesheet scenario (estimate vs consuntivo)
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

    # --- Integration: mapping conto->elemento + classification rule ---
    exp_account = env["account.account"].create(
        {"name": "Servizi esterni", "code": "600500", "account_type": "expense",
         "cmc_cost_element_id": el_mat.id}
    )
    env["cmc.classification.rule"].create(
        {"name": "Servizi esterni -> variabile diretto",
         "general_account_id": exp_account.id,
         "cmc_behavior": "variable", "cmc_directness": "direct"}
    )
    # an analytic line posted from that account gets auto-classified
    env["account.analytic.line"].create(
        {"name": "Servizio esterno gennaio", "account_id": z.id, "amount": -450,
         "date": JAN15, "general_account_id": exp_account.id}
    )

    cr.commit()
    print("SEED OK: closing=%s costing=%s abc=%s joint=%s tdabc=%s"
          % (closing.id, costing.id, abc.id, joint.id, tdabc.id))
