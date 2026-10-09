#!/usr/bin/env python
"""Extend the Delta Servizi 2026 dataset with the COMMESSA (project P&L) layer:
projects, employees, timesheets (fogli ore), customer invoices (ricavi +
scritture contabili) and vendor bills (costi), then a margin-per-project recap.

Runs on company 'Delta Servizi S.r.l.' created by cmc_seed_year.py. Loads the
generic chart of accounts on that company if missing. Idempotent: skips if the
commessa projects already exist.
"""
import datetime
import sys

sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/odoo")
sys.path.insert(0, "/Volumes/WDEXSSD/2ST/05_DEV/odoo19")

import odoo
from odoo import api
from odoo.tools import config

config.parse_config(["-c", "/Volumes/WDEXSSD/2ST/05_DEV/odoo19/config/cmc_all.conf"])
YEAR = 2026
SEASON = {1: 0.90, 2: 0.95, 3: 1.05, 4: 1.00, 5: 1.05, 6: 1.10,
          7: 0.85, 8: 0.50, 9: 1.05, 10: 1.15, 11: 1.20, 12: 1.00}


def mid(m):
    return datetime.date(YEAR, m, 15)


def q_end(q):
    m = q * 3
    if m == 12:
        return datetime.date(YEAR, 12, 31)
    return datetime.date(YEAR, m + 1, 1) - datetime.timedelta(days=1)


registry = odoo.modules.registry.Registry("cmc_all")
with registry.cursor() as cr:
    env = api.Environment(cr, odoo.SUPERUSER_ID, {})

    company = env["res.company"].search([("name", "=", "Delta Servizi S.r.l.")], limit=1)
    if not company:
        print("ABORT: run cmc_seed_year.py first (company Delta missing)")
        sys.exit(1)
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))

    if env["project.project"].search_count(
            [("name", "=", "Commessa CRM - Cliente A"), ("company_id", "=", company.id)]):
        cr.commit()
        print("COMMESSE SEED SKIP: already present")
        sys.exit(0)

    # ---- 1) chart of accounts on Delta (generic) ----
    if not env["account.journal"].search_count([("company_id", "=", company.id)]):
        env["account.chart.template"].try_loading(
            "generic_coa", company=company, install_demo=False)
        print("  loaded generic_coa on Delta")

    def _acc(atype):
        return env["account.account"].search(
            [("account_type", "=", atype), ("company_ids", "in", company.id)],
            limit=1)

    acc_income = _acc("income")
    acc_expense = _acc("expense")
    sale_journal = env["account.journal"].search(
        [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1)
    purch_journal = env["account.journal"].search(
        [("type", "=", "purchase"), ("company_id", "=", company.id)], limit=1)

    # ---- 2) employees (consultants) with hourly cost ----
    def emp(name, cost):
        return env["hr.employee"].create(
            {"name": name, "company_id": company.id, "hourly_cost": cost})

    consultants = {
        "sw": [emp("Marco Rossi", 62), emp("Sara Bianchi", 55),
               emp("Luca Verdi", 48)],
        "data": [emp("Elena Conti", 70), emp("Paolo Neri", 58)],
        "adv": [emp("Giulia Fabbri", 75), emp("Andrea Gallo", 52)],
    }

    # existing cost-object products + customers from the base year seed
    def prod(name):
        return env["product.product"].search([("name", "=", name)], limit=1)

    def part(name):
        return env["res.partner"].search(
            [("name", "=", name), ("company_id", "=", company.id)], limit=1)

    P = {
        "crm": (prod("Progetto CRM - Cliente A"), part("Cliente A S.p.A."), "sw"),
        "data": (prod("Piattaforma Dati - Cliente B"), part("Cliente B S.r.l."), "data"),
        "adv": (prod("Advisory Retainer - Cliente C"), part("Cliente C Group"), "adv"),
        "web": (prod("Portale Web - Cliente D"), part("Cliente D Retail"), "sw"),
    }

    plan = env["account.analytic.plan"].search(
        [("parent_id", "=", False)]
    ).filtered(lambda p: p._column_name() == "account_id")[:1]

    # ---- 3) projects (commesse) + analytic account + task ----
    # yearly revenue target and labor-hours scale per commessa
    SPEC = {
        "crm": dict(name="Commessa CRM - Cliente A", revenue=380000, hours=180),
        "data": dict(name="Commessa Piattaforma Dati - Cliente B", revenue=340000, hours=150),
        "adv": dict(name="Commessa Advisory - Cliente C", revenue=260000, hours=120),
        "web": dict(name="Commessa Portale Web - Cliente D", revenue=150000, hours=90),
    }
    projects = {}
    for key, spec in SPEC.items():
        product, customer, team = P[key]
        aa = env["account.analytic.account"].create(
            {"name": spec["name"], "plan_id": plan.id, "company_id": company.id,
             "partner_id": customer.id})  # no cmc_center_type -> ignored by CMC engines
        pr = env["project.project"].create(
            {"name": spec["name"], "company_id": company.id,
             "partner_id": customer.id, "allow_timesheets": True,
             "account_id": aa.id})
        task = env["project.task"].create(
            {"name": "Delivery", "project_id": pr.id, "company_id": company.id})
        projects[key] = dict(project=pr, task=task, account=aa,
                             product=product, customer=customer, team=team,
                             **spec)

    # ---- 4) timesheets (fogli ore) monthly per commessa ----
    ts_count = 0
    for key, d in projects.items():
        team = consultants[d["team"]]
        # monthly hours ~ yearly hours * season, split across the team
        for m in range(1, 13):
            month_h = d["hours"] * SEASON[m]
            share = month_h / len(team)
            for e in team:
                env["account.analytic.line"].create(
                    {"name": "Timesheet %s %02d/%d" % (e.name, m, YEAR),
                     "project_id": d["project"].id, "task_id": d["task"].id,
                     "employee_id": e.id, "date": mid(m),
                     "unit_amount": round(share, 1), "company_id": company.id})
                ts_count += 1

    # ---- 5) customer invoices (ricavi + scritture) quarterly per commessa ----
    inv_ids = []
    for key, d in projects.items():
        analytic = {str(d["account"].id): 100.0}
        for q in range(1, 5):
            dte = q_end(q)
            amount = d["revenue"] / 4.0 * SEASON[dte.month]
            mv = env["account.move"].create(
                {"move_type": "out_invoice", "partner_id": d["customer"].id,
                 "company_id": company.id, "journal_id": sale_journal.id,
                 "invoice_date": dte, "date": dte,
                 "invoice_line_ids": [(0, 0, {
                     "product_id": d["product"].id,
                     "name": "%s - Q%d %d" % (d["name"], q, YEAR),
                     "quantity": 1.0, "price_unit": round(amount, 2),
                     "account_id": acc_income.id, "tax_ids": [(5, 0, 0)],
                     "analytic_distribution": analytic})]})
            mv.action_post()
            inv_ids.append(mv.id)

    # ---- 6) vendor bills (costi esterni: subappalti/cloud) quarterly on some ----
    supplier = env["res.partner"].create(
        {"name": "Fornitore Subappalti S.r.l.", "company_id": company.id})
    bill_ids = []
    for key in ("crm", "data", "web"):
        d = projects[key]
        analytic = {str(d["account"].id): 100.0}
        for q in range(1, 5):
            dte = q_end(q)
            amount = d["revenue"] / 4.0 * 0.12 * SEASON[dte.month]  # ~12% subcontract
            mv = env["account.move"].create(
                {"move_type": "in_invoice", "partner_id": supplier.id,
                 "company_id": company.id, "journal_id": purch_journal.id,
                 "invoice_date": dte, "date": dte,
                 "invoice_line_ids": [(0, 0, {
                     "name": "Subappalto %s Q%d" % (d["name"], q),
                     "quantity": 1.0, "price_unit": round(amount, 2),
                     "account_id": acc_expense.id, "tax_ids": [(5, 0, 0)],
                     "analytic_distribution": analytic})]})
            mv.action_post()
            bill_ids.append(mv.id)

    cr.commit()

    # ---- 7) margin per commessa recap ----
    print("COMMESSE SEED OK: company=%s" % company.name)
    print("  employees=%s  timesheets=%s  invoices=%s  bills=%s"
          % (sum(len(v) for v in consultants.values()), ts_count,
             len(inv_ids), len(bill_ids)))
    print("  %-38s %12s %12s %12s %12s" %
          ("Commessa", "Ricavi", "Costo lavoro", "Costi est.", "Margine"))
    for key, d in projects.items():
        aa = d["account"]
        revenue = sum(env["account.move.line"].search([
            ("move_id.move_type", "=", "out_invoice"),
            ("move_id.state", "=", "posted"),
            ("account_id.account_type", "=", "income"),
            ("company_id", "=", company.id),
        ]).filtered(lambda l, a=aa: str(a.id) in (l.analytic_distribution or {}))
            .mapped("balance"))
        revenue = -revenue  # income is credit (negative balance)
        labor = -sum(env["account.analytic.line"].search(
            [("project_id", "=", d["project"].id)]).mapped("amount"))
        ext = sum(env["account.move.line"].search([
            ("move_id.move_type", "=", "in_invoice"),
            ("move_id.state", "=", "posted"),
            ("account_id.account_type", "=", "expense"),
            ("company_id", "=", company.id),
        ]).filtered(lambda l, a=aa: str(a.id) in (l.analytic_distribution or {}))
            .mapped("balance"))
        margin = revenue - labor - ext
        print("  %-38s %12.0f %12.0f %12.0f %12.0f"
              % (d["name"][:38], revenue, labor, ext, margin))
