#!/usr/bin/env python
"""Seed a REALISTIC full-year (2026) dataset for a project-based services firm.

Creates a dedicated company 'Delta Servizi S.r.l.' inside cmc_all and populates
12 months of analytic cost lines + quarterly runs for every CMC methodology
(ribaltamento, full costing, ABC, TDABC, direct costing, capacity) so the same
underlying costs can be viewed through each lens. Idempotent: skips if the
company already exists.

Run:
  /opt/homebrew/Caskroom/miniconda/base/envs/odoo-19/bin/python \
      scripts/cmc_seed_year.py
"""
import datetime
import sys

sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/odoo")
sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19")

import odoo
from odoo import api
from odoo.tools import config

DB = "cmc_all"
config.parse_config(["-c", "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/config/cmc_all.conf"])

YEAR = 2026
# services seasonality: summer dip (Aug), Q4 ramp
SEASON = {1: 0.90, 2: 0.95, 3: 1.05, 4: 1.00, 5: 1.05, 6: 1.10,
          7: 0.85, 8: 0.50, 9: 1.05, 10: 1.15, 11: 1.20, 12: 1.00}


def mid(month):
    return datetime.date(YEAR, month, 15)


def q_range(q):
    m0 = (q - 1) * 3 + 1
    start = datetime.date(YEAR, m0, 1)
    end_month = m0 + 2
    if end_month == 12:
        end = datetime.date(YEAR, 12, 31)
    else:
        end = datetime.date(YEAR, end_month + 1, 1) - datetime.timedelta(days=1)
    return start, end


registry = odoo.modules.registry.Registry(DB)
with registry.cursor() as cr:
    env = api.Environment(cr, odoo.SUPERUSER_ID, {})

    CO_NAME = "Delta Servizi S.r.l."
    if env["res.company"].search_count([("name", "=", CO_NAME)]):
        cr.commit()
        print("YEAR SEED SKIP: company '%s' already present" % CO_NAME)
        sys.exit(0)

    company = env["res.company"].create({"name": CO_NAME})
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))

    # make admin able to see the new company + CMC manager
    admin = env.ref("base.user_admin")
    admin.company_ids = [(4, company.id)]
    admin.group_ids = [(4, env.ref("cmc_base.group_cmc_manager").id)]

    cur = company.currency_id

    # ---- master data (company-scoped: elements have check_company) ----
    def element(code, name, kind=False):
        vals = {"name": name, "code": code, "company_id": company.id}
        if kind:
            vals["cmc_kind"] = kind
        return env["cmc.cost.element"].create(vals)

    el_mod = element("MOD", "Manodopera consulenti")
    el_mp = element("MP", "Materiali/licenze")
    el_amm = element("AMM", "Ammortamenti")
    el_cloud = element("CLOUD", "Cloud & SaaS")
    el_trav = element("TRAV", "Trasferte")
    el_sub = element("SUB", "Subappalti")
    el_fig = element("INT", "Interessi di computo", kind="figurative")

    def driver(code, name):
        return env["cmc.cost.driver"].create(
            {"name": name, "code": code, "company_id": company.id})

    d_hours = driver("ORE", "Ore consulenza")
    d_proj = driver("PROJ", "N. progetti")
    d_tick = driver("TICK", "N. ticket")

    plan = env["account.analytic.plan"].search(
        [("parent_id", "=", False)]
    ).filtered(lambda p: p._column_name() == "account_id")[:1]

    def center(name, ctype=False, period=False):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": plan.id, "company_id": company.id,
             "cmc_center_type": ctype, "cmc_is_period": period})

    # auxiliary (support) centers -> reallocated onto delivery
    it = center("IT & Sistemi", "auxiliary")
    staff = center("Direzione & Staff", "auxiliary")
    # final / delivery units
    sw = center("Delivery Software", "final")
    data = center("Delivery Data/AI", "final")
    adv = center("Delivery Advisory", "final")
    # period (not absorbed by products)
    comm = center("Commerciale & BD", period=True)

    delivery = [sw, data, adv]

    # ---- cost objects: service lines (products) + customers (partners) ----
    def product(name, price):
        return env["product.product"].with_company(company).create(
            {"name": name, "standard_price": price, "type": "service"})

    prj_crm = product("Progetto CRM - Cliente A", 0)
    prj_data = product("Piattaforma Dati - Cliente B", 0)
    prj_adv = product("Advisory Retainer - Cliente C", 0)
    prj_web = product("Portale Web - Cliente D", 0)
    prj_maint = product("Manutenzione & Supporto", 0)
    products = prj_crm | prj_data | prj_adv | prj_web | prj_maint

    def partner(name):
        return env["res.partner"].create({"name": name, "company_id": company.id})

    cli_a = partner("Cliente A S.p.A.")
    cli_b = partner("Cliente B S.r.l.")
    cli_c = partner("Cliente C Group")
    cli_d = partner("Cliente D Retail")

    # ---- ABC activities (stage-1 pools tagged on labor lines) ----
    act_dev = env["cmc.abc.activity"].create(
        {"name": "Sviluppo & delivery", "code": "DEV", "company_id": company.id})
    act_pm = env["cmc.abc.activity"].create(
        {"name": "Project management", "code": "PM", "company_id": company.id})
    act_qa = env["cmc.abc.activity"].create(
        {"name": "Test & QA", "code": "QA", "company_id": company.id})
    act_sup = env["cmc.abc.activity"].create(
        {"name": "Supporto clienti", "code": "SUP", "company_id": company.id})

    # ---- TDABC resources / characteristics / activities ----
    # consultant pool: cost/period fixed per period, capacity in minutes
    # 20 consultants * ~1700 productive h/yr -> quarterly practical below
    res_team = env["cmc.tdabc.resource"].create(
        {"name": "Team Consulenti", "cost": 300000.0,
         "theoretical_time": 20 * 1700 * 60 / 4,  # minutes/quarter theoretical
         "util_factor": 80, "company_id": company.id})
    char_vip = env["cmc.tdabc.characteristic"].create(
        {"code": "VIP", "name": "Cliente strategico", "ttype": "bool",
         "company_id": company.id})
    char_cx = env["cmc.tdabc.characteristic"].create(
        {"code": "CX", "name": "Complessità (1-5)", "ttype": "number",
         "company_id": company.id})
    # characteristic values on cost objects (products + a customer)
    prj_crm.product_tmpl_id.tdabc_char_value_ids = [
        (0, 0, {"characteristic_id": char_cx.id, "value": 4.0,
                "company_id": company.id}),
        (0, 0, {"characteristic_id": char_vip.id, "value": 1.0,
                "company_id": company.id})]
    prj_data.product_tmpl_id.tdabc_char_value_ids = [
        (0, 0, {"characteristic_id": char_cx.id, "value": 5.0,
                "company_id": company.id})]
    prj_web.product_tmpl_id.tdabc_char_value_ids = [
        (0, 0, {"characteristic_id": char_cx.id, "value": 2.0,
                "company_id": company.id})]
    cli_a.tdabc_char_value_ids = [
        (0, 0, {"characteristic_id": char_vip.id, "value": 1.0,
                "company_id": company.id})]

    t_setup = env["cmc.tdabc.activity"].create(
        {"name": "Setup & analisi", "code": "TSET", "resource_id": res_team.id,
         "base_time": 120, "company_id": company.id,
         "term_ids": [(0, 0, {"characteristic_id": char_cx.id, "coefficient": 30}),
                      (0, 0, {"characteristic_id": char_vip.id,
                              "coefficient": 60})]})
    t_build = env["cmc.tdabc.activity"].create(
        {"name": "Realizzazione", "code": "TBLD", "resource_id": res_team.id,
         "base_time": 300, "company_id": company.id,
         "term_ids": [(0, 0, {"characteristic_id": char_cx.id,
                              "coefficient": 90})]})
    t_supp = env["cmc.tdabc.activity"].create(
        {"name": "Supporto", "code": "TSUP", "resource_id": res_team.id,
         "base_time": 40, "company_id": company.id})

    # ---------- helper: create an analytic cost line ----------
    def cost(c, amount, el, behavior="fixed", month=6, abc_act=None,
             tdabc_act=None, hours=0.0):
        vals = {"name": "%s %02d/%d" % (el.code, month, YEAR),
                "account_id": c.id, "company_id": company.id,
                "amount": -round(amount, 2), "date": mid(month),
                "cmc_behavior": behavior, "cmc_cost_element_id": el.id}
        if abc_act:
            vals["cmc_activity_id"] = abc_act.id
        if tdabc_act:
            vals["cmc_tdabc_activity_id"] = tdabc_act.id
            vals["unit_amount"] = hours
        return env["account.analytic.line"].create(vals)

    # base monthly labor cost per delivery center (€), pre-seasonality
    BASE_LABOR = {sw.id: 42000, data.id: 36000, adv.id: 24000}
    # ---------- 12 months of raw cost lines ----------
    for m in range(1, 13):
        sf = SEASON[m]
        for c in delivery:
            lab = BASE_LABOR[c.id] * sf
            # split labor across ABC activities (tagged) so ABC pools are fed
            cost(c, lab * 0.55, el_mod, "variable", m, abc_act=act_dev)
            cost(c, lab * 0.15, el_mod, "variable", m, abc_act=act_pm)
            cost(c, lab * 0.15, el_mod, "variable", m, abc_act=act_qa)
            cost(c, lab * 0.15, el_mod, "variable", m, abc_act=act_sup)
            # non-labor variable costs
            cost(c, lab * 0.08, el_cloud, "variable", m)
            cost(c, lab * 0.05, el_trav, "variable", m)
            if m in (3, 6, 9, 11):
                cost(c, lab * 0.20, el_sub, "variable", m)
            # fixed depreciation (equipment/licenses)
            cost(c, 3000, el_amm, "fixed", m)
        # auxiliary centers monthly pools (to be reallocated)
        cost(it, 12000 * sf, el_mod, "fixed", m)
        cost(it, 4000, el_amm, "fixed", m)
        cost(staff, 18000, el_mod, "fixed", m)
        # period (commercial) cost - never absorbed by products
        cost(comm, 9000 * sf, el_mod, "fixed", m)

        # TDABC actual hours (consuntivo) tagged per activity, monthly
        cost(sw, 0, el_mod, "variable", m, tdabc_act=t_build,
             hours=1400 * sf)
        cost(data, 0, el_mod, "variable", m, tdabc_act=t_setup,
             hours=350 * sf)
        cost(adv, 0, el_mod, "variable", m, tdabc_act=t_supp,
             hours=500 * sf)

    # ---------- center services (reallocation drivers) ----------
    Svc = env["cmc.center.service"]
    # IT reallocated by hours of service, Staff by n. projects
    for c, qty in ((sw, 900), (data, 700), (adv, 400)):
        Svc.create({"provider_id": it.id, "receiver_id": c.id,
                    "driver_id": d_hours.id, "quantity": qty,
                    "company_id": company.id})
    for c, qty in ((sw, 4), (data, 3), (adv, 2)):
        Svc.create({"provider_id": staff.id, "receiver_id": c.id,
                    "driver_id": d_proj.id, "quantity": qty,
                    "company_id": company.id})

    # ---------- allocation: shared office energy split by hours ----------
    env["cmc.allocation"].create(
        {"name": "Energia sede (comune)", "source_account_id": sw.id,
         "driver_id": d_hours.id, "amount": 24000, "company_id": company.id,
         "line_ids": [(0, 0, {"target_account_id": sw.id, "quantity": 900}),
                      (0, 0, {"target_account_id": data.id, "quantity": 700}),
                      (0, 0, {"target_account_id": adv.id, "quantity": 400})]})

    # ---------- figurative cost: notional interest on WIP ----------
    env["cmc.figurative.cost"].create(
        {"name": "Interessi di computo su WIP", "cost_element_id": el_fig.id,
         "method": "interest", "capital": 150000, "rate": 0.04,
         "cost_object_ref": "product.product,%s" % prj_data.id,
         "cmc_directness": "direct", "company_id": company.id})

    made = {"closing": [], "costing": [], "abc": [], "tdabc": [],
            "capacity": [], "direct": []}

    # product driver quantities per delivery center (ore) for costing
    COSTING_MIX = [
        (sw, prj_crm, 320), (sw, prj_web, 180), (sw, prj_maint, 90),
        (data, prj_data, 380), (data, prj_crm, 60),
        (adv, prj_adv, 300), (adv, prj_maint, 60),
    ]
    ABC_MIX = [
        (act_dev, prj_crm, 40), (act_dev, prj_data, 55), (act_dev, prj_web, 25),
        (act_pm, prj_crm, 12), (act_pm, prj_data, 14), (act_pm, prj_adv, 10),
        (act_qa, prj_crm, 18), (act_qa, prj_data, 20), (act_qa, prj_web, 8),
        (act_sup, prj_maint, 60), (act_sup, prj_adv, 30),
    ]

    for q in range(1, 5):
        df, dt = q_range(q)
        qn = "%d Q%d" % (YEAR, q)

        # 1) ribaltamento (step-down) for the quarter
        closing = env["cmc.closing.run"].create(
            {"name": "Chiusura %s" % qn, "method": "step_down",
             "date_from": df, "date_to": dt, "company_id": company.id})
        closing.action_compute()
        made["closing"].append(closing.id)

        # 2) full costing tied to that closing (multiple base)
        costing = env["cmc.costing.run"].create(
            {"name": "Costo pieno %s" % qn, "closing_run_id": closing.id,
             "base": "multiple", "date_from": df, "date_to": dt,
             "company_id": company.id,
             "product_ids": [(6, 0, products.ids)],
             "line_ids": [
                 (0, 0, {"center_id": c.id, "product_id": p.id,
                         "driver_id": d_hours.id, "driver_qty": qty})
                 for c, p, qty in COSTING_MIX]})
        costing.action_compute()
        made["costing"].append(costing.id)

        # 3) ABC for the quarter (activity pools from tagged labor)
        abc = env["cmc.abc.run"].create(
            {"name": "ABC %s" % qn, "date_from": df, "date_to": dt,
             "company_id": company.id,
             "line_ids": [
                 (0, 0, {"activity_id": a.id, "product_id": p.id,
                         "driver_id": d_hours.id, "driver_qty": qty})
                 for a, p, qty in ABC_MIX]})
        abc.action_compute()
        made["abc"].append(abc.id)

        # 4) TDABC for the quarter (estimate vs timesheet)
        tdabc = env["cmc.tdabc.run"].create(
            {"name": "TDABC %s" % qn, "date_from": df, "date_to": dt,
             "company_id": company.id,
             "line_ids": [
                 (0, 0, {"activity_id": t_setup.id, "cost_object_type": "product",
                         "product_id": prj_data.id, "volume": 40}),
                 (0, 0, {"activity_id": t_build.id, "cost_object_type": "product",
                         "product_id": prj_crm.id, "volume": 120}),
                 (0, 0, {"activity_id": t_build.id, "cost_object_type": "product",
                         "product_id": prj_web.id, "volume": 80}),
                 (0, 0, {"activity_id": t_supp.id, "cost_object_type": "customer",
                         "partner_id": cli_a.id, "volume": 200}),
                 (0, 0, {"activity_id": t_supp.id, "cost_object_type": "customer",
                         "partner_id": cli_d.id, "volume": 150}),
             ]})
        tdabc.action_compute()
        made["tdabc"].append(tdabc.id)

        # 5) capacity analysis per delivery center (hours)
        for c, th, pr, ac in ((sw, 3600, 3200, 3000 * SEASON[df.month]),
                              (data, 2800, 2500, 2300 * SEASON[df.month]),
                              (adv, 2000, 1800, 1600 * SEASON[df.month])):
            cap = env["cmc.capacity.analysis"].create(
                {"center_id": c.id, "date_from": df, "date_to": dt,
                 "driver_id": d_hours.id, "capacity_theoretical": th,
                 "capacity_practical": pr, "capacity_actual": round(ac),
                 "company_id": company.id})
            made["capacity"].append(cap.id)

        # 6) direct costing (contribution margin) per quarter
        dc = env["cmc.direct.costing"].create(
            {"name": "Direct Costing %s" % qn, "common_fixed_cost": 45000,
             "company_id": company.id,
             "line_ids": [
                 (0, 0, {"name": "Delivery Software",
                         "revenue": 210000 * SEASON[df.month],
                         "variable_cost": 120000 * SEASON[df.month],
                         "specific_fixed_cost": 30000}),
                 (0, 0, {"name": "Delivery Data/AI",
                         "revenue": 180000 * SEASON[df.month],
                         "variable_cost": 100000 * SEASON[df.month],
                         "specific_fixed_cost": 26000}),
                 (0, 0, {"name": "Delivery Advisory",
                         "revenue": 120000 * SEASON[df.month],
                         "variable_cost": 60000 * SEASON[df.month],
                         "specific_fixed_cost": 18000}),
             ]})
        made["direct"].append(dc.id)

    # ---------- annual CVR / break-even per service line ----------
    for name, p, v, fc, vol in (
            ("Break-even Delivery Software", 850, 480, 900000, 1400),
            ("Break-even Delivery Data/AI", 780, 470, 700000, 1150),
            ("Break-even Delivery Advisory", 900, 400, 480000, 1000)):
        env["cmc.cvr.analysis"].create(
            {"name": name, "price_unit": p, "variable_unit": v,
             "fixed_cost": fc, "volume": vol, "target_profit": 60000,
             "company_id": company.id})

    n_lines = env["account.analytic.line"].search_count(
        [("company_id", "=", company.id)])
    cr.commit()
    print("YEAR SEED OK: company=%s (id=%s)" % (CO_NAME, company.id))
    print("  analytic lines: %s" % n_lines)
    print("  closings=%s costings=%s abc=%s tdabc=%s capacity=%s direct=%s cvr=3"
          % (len(made["closing"]), len(made["costing"]), len(made["abc"]),
             len(made["tdabc"]), len(made["capacity"]), len(made["direct"])))
