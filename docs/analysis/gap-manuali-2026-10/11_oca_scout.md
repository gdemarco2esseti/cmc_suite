# OCA scout (19.0) — sintesi
- Budget: account_budget_oca 19.0 (base), mis_builder_budget 19.0 (budget in MIS). Nessun modulo OCA per versioni budget / flexible budget / forecast.
- MIS: mis_builder 19.0 (depends account, board, report_xlsx, date_range), mis_builder_cash_flow 19.0. mis_template_financial_report SOLO 18 (port).
- IT: l10n_it_financial_statements_report 19.0 (bilancio CEE a sezioni contrapposte, depends account_financial_report) — NON fa riclassificazione finanziaria/funzionale né indici.
- Margini: product_standard_margin, sale_report_margin, account_invoice_margin(_sale) 19.0.
- Analytic: account_analytic_parent SOLO 18 (port). Progetto: project_budget SOLO 18; EVM nessuno.
- Scoperti in OCA 19: transfer pricing, EVM, budget versions, flexible budget, cost-center mgmt.
- Enterprise: account_budget, project_account_budget, account_reports.

# Odoo scout (core/EE)
- Budget analitico: SOLO EE (account_budget: budget.analytic/budget.line/budget.report; project_account_budget). Core: nulla.
- Report finanziari: SOLO EE (account_reports, engine domain/aggregation/custom). l10n_it_reports EE = SP/CE civilistici, NO riclassificazioni/indici.
- Project profitability panel CORE (project_project.py:987-1128); EVM assente.
- sale_margin CORE (margin su SO line). Cost method standard/FIFO/AVCO CORE; mrp_account total_cost CORE; varianze produzione assenti.
- spreadsheet_dashboard CORE. Analitico solo valuta aziendale.
=> Suite CMC è CE-first (unico ponte EE: cmc_timesheet_grid): i nuovi moduli NON dipendono da EE; eventuali ponti EE opzionali.
