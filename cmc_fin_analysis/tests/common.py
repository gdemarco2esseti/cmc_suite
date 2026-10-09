"""WP-13 ``cmc_fin_analysis`` - analisi di bilancio (FIN-01..FIN-11, AD-116, AD-118). Shared fixture.

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Module ``cmc_fin_analysis``, depends ``["cmc_account"]`` only. GL read ONLY through the ``cmc_account``
readers (``_cmc_gl_balances_at`` / ``_cmc_gl_balance_read_group``, AD-111), aggregated (AD-114): the number
of queries of a compute does not depend on the number of journal items.
Every model below has ``company_id``, ``_check_company_auto = True`` and a global ``ir.rule``
``['|', ('company_id', '=', False), ('company_id', 'parent_of', company_ids)]`` NOT in noupdate (AD-115).

Configuration models (shared rows have ``company_id = False``)
- ``cmc.fin.scheme``: ``name`` (translate), ``code`` Char req (unique per COALESCE(company,0)),
  ``scheme_type`` Selection ``balance_sheet`` / ``income_statement`` / ``cash_flow`` (req), ``sequence``,
  ``active``, ``company_id``, ``base_code`` Char (line code = 100% for common-size),
  ``line_ids`` -> ``cmc.fin.scheme.line``, ``rule_ids`` -> ``cmc.fin.mapping.rule``.
- ``cmc.fin.scheme.line``: ``scheme_id`` (req, cascade), ``sequence``, ``code`` Char req (``str.isidentifier()``
  else ValidationError; unique per scheme), ``name``, ``line_type`` ``account`` (fed by mapping) / ``formula``,
  ``formula`` Char (safe_eval expression, validated with ``odoo.tools.safe_eval.test_python_expr`` on
  create/write -> ValidationError), ``sign`` Integer 1 / -1 (presentation value = sign x natural balance,
  debit positive), ``result_line`` Boolean (BS: receives ``-(cumulative P&L balance up to the balance date)``
  = year result + prior results not closed to equity; on a history year = -(sum of its P&L rows)).
- ``cmc.fin.mapping.rule``: ``scheme_id`` (req), ``company_id``, ``sequence`` (default 10), ``line_id`` (req,
  an ``account`` line of the same scheme), criteria AND-ed, at least one (else ValidationError):
  ``account_id``, ``account_code_prefix``, ``account_type`` (selection of ``account.account.account_type``),
  ``cmc_behavior`` (fixed/variable/semi_variable = the behavior assigned to the account by the first active
  ``cmc.classification.rule`` of the company or shared, by (sequence, id), matching through
  ``general_account_id`` or ``account_code_prefix``). First matching rule by (sequence, id) among the scheme
  rules with company False or = run company wins. ``balance_sheet`` schemes map accounts with
  ``include_initial_balance``, ``income_statement`` schemes the others; ``off_balance`` is ignored.
- ``cmc.fin.override`` (choices register, FIN-02): ``company_id`` req, ``scheme_id``, ``year`` Integer,
  ``account_id``, ``line_id`` (target ``account`` line of ``scheme_id``, else ValidationError), ``amount`` Float
  (absolute part of the account balance moved, same sign as the balance; 0 = the whole balance), ``reason``
  Text req (blank -> ValidationError). Applied after the mapping, on the year-end of ``year``.
- ``cmc.fin.ratio``: ``code`` (identifier, unique per COALESCE(company,0)), ``name``, ``sequence``, ``area``
  liquidity/solidity/profitability/cash_flow/crisis/value, ``expression`` (validated like the formula),
  ``unit`` ratio/percent/days/amount, ``threshold_op`` ``>`` / ``>=`` / ``<`` / ``<=`` (or False),
  ``threshold`` Float, ``full_only`` Boolean, ``company_id``, ``active``.
- ``cmc.fin.history`` (historical import, AD-116): ``company_id`` (default env company), ``year``,
  ``account_id`` (check_company), ``balance`` (natural sign; pre-closing trial balance of the year: BS
  accounts at year end WITHOUT the year result, P&L accounts for the year). UniqueIndex (company, year,
  account) -> IntegrityError. Importable with the standard ``load()`` (account given by its code).
- ``cmc.fin.input`` (typed reference data): ``company_id``, ``year``, ``code`` Char, ``value``; unique
  (company, year, code). Codes read by the shipped ratios: ``rf``, ``beta``, ``rm``, ``tax_rate``, ``employees``.
- ``cmc.fin.benchmark`` (sector benchmark, FIN-05): ``company_id``, ``ateco_code`` Char req, ``year``,
  ``ratio_id``, ``value``.

Run ``cmc.fin.analysis`` (``_inherit = ["cmc.run.mixin"]``, AD-113: one run per company x span)
- ``name``, ``company_id`` (req, default env company), ``year_from``, ``year_to`` Integer req (to < from ->
  ValidationError), ``scheme_ids`` M2m (default: the active schemes visible to the company),
  ``reduced_mode`` Boolean, ``ateco_code`` Char. ``_cmc_input_fields`` = company_id, year_from, year_to,
  scheme_ids, reduced_mode, ateco_code; ``_cmc_result_fields`` = value_ids, ratio_value_ids.
- Year Y = the company fiscal year ending in calendar year Y (balance date = its last day). Source of Y:
  ``history`` when ``cmc.fin.history`` rows exist for (company, Y) - the GL is then NOT read for Y - else
  ``gl``. A history year whose balances do not sum to 0 -> UserError at compute.
- Compute, per year: (1) account lines of every run scheme (0 when nothing maps: every account line of
  every year gets a row), overrides applied, result line filled; (2) formula lines, schemes by ``sequence``
  then lines by ``sequence``; (3) active ratios visible to the company, by ``sequence``.
  Namespace (formulas and ratios): line codes of the run schemes for the year, ``prev_<code>`` (previous run
  year), ``cmc.fin.input`` codes of the year, ratio codes already evaluated. A code defined twice (two
  schemes, or a ratio and a line) -> UserError. A non-zero balance on an account no rule of a scheme of the
  right type matches -> UserError naming the account code(s).
  Formula line raising NameError / ZeroDivisionError -> NO value row for that year. Ratio in that case, or
  ``full_only`` in reduced mode -> row with ``status = "not_computable"`` and ``value = 0``.
- Results (``_inherit = ["cmc.run.result.mixin"]``; written in sudo under the run sentinel):
  ``value_ids`` -> ``cmc.fin.analysis.value``: ``run_id``, ``company_id`` (related stored), ``year``, ``scheme_id``,
  ``line_id``, ``code``, ``value``, ``source`` gl/history, ``common_size_pct`` (value / value of the scheme
  ``base_code`` same year x 100; 0 without base), ``yoy_pct`` ((X_t - X_t-1) / X_t-1 x 100),
  ``yoy_computable`` (False on the first run year or when X_t-1 = 0).
  ``ratio_value_ids`` -> ``cmc.fin.ratio.value``: ``run_id``, ``company_id``, ``year``, ``ratio_id``, ``code``,
  ``value``, ``status`` ok/alert/not_computable (alert = threshold set and ``value <op> threshold`` false),
  ``benchmark`` + ``has_benchmark`` (``cmc.fin.benchmark`` of the run ``ateco_code``, year and ratio).

Shipped data (xmlid ``cmc_fin_analysis.<id>``; formulas over the namespace above)
- ``scheme_bs_functional`` (balance_sheet, base ``total_assets``): financial AND functional SP in one scheme.
  account lines sign +1: intangible, tangible, other_op_nc, fin_assets_nc, inv_raw, inv_finished, receivables,
  other_op_c, fin_assets_c, cash; sign -1: equity (result_line), op_liab_nc, fin_debt_nc, payables,
  other_op_liab_c, fin_debt_c. formulas: op_assets_nc = intangible+tangible+other_op_nc;
  inventory = inv_raw+inv_finished; op_assets_c = inventory+receivables+other_op_c;
  assets_nc = op_assets_nc+fin_assets_nc; assets_c = op_assets_c+fin_assets_c+cash;
  total_assets = assets_nc+assets_c; op_liab_c = payables+other_op_liab_c; liab_nc = op_liab_nc+fin_debt_nc;
  liab_c = op_liab_c+fin_debt_c; total_sources = equity+liab_nc+liab_c; net_fixed = op_assets_nc-op_liab_nc;
  ccnop = op_assets_c-op_liab_c; coin = net_fixed+ccnop; fin_assets = fin_assets_nc+fin_assets_c+cash;
  cin = coin+fin_assets; fin_debt = fin_debt_nc+fin_debt_c; pfn = fin_debt-fin_assets;
  pfn_nc = fin_debt_nc-fin_assets_nc; pfn_c = fin_debt_c-fin_assets_c-cash.
  default rules (shared, sequence >= 100): asset_fixed->tangible, asset_non_current->other_op_nc,
  asset_receivable->receivables, asset_current & asset_prepayments->other_op_c, asset_cash->cash,
  liability_payable->payables, liability_current->other_op_liab_c, liability_credit_card->fin_debt_c,
  liability_non_current->fin_debt_nc, equity & equity_unaffected->equity.
- ``scheme_pl_value_added`` (income_statement, base ``revenue``): account lines sign -1: revenue,
  fg_inv_change, other_revenue, fin_income, extraordinary; sign +1: purchases, raw_inv_change, services,
  other_expenses, personnel, severance, depreciation, provisions, fin_charges, taxes. formulas:
  production_value = revenue+fg_inv_change+other_revenue; external_costs = purchases+raw_inv_change+services
  +other_expenses; value_added = production_value-external_costs; ebitda = value_added-personnel;
  non_cash = severance+depreciation+provisions; mon = ebitda-non_cash; ebit = mon+fin_income;
  ordinary_result = ebit-fin_charges; pre_tax = ordinary_result+extraordinary; net_income = pre_tax-taxes;
  raw_consumption = purchases+raw_inv_change; gross_cash_flow = net_income+non_cash.
  default rules: income->revenue, income_other->other_revenue, expense_direct_cost->purchases,
  expense->services, expense_depreciation->depreciation.
- ``scheme_pl_cost_of_sales`` (base ``cs_revenue``): account cs_revenue (-1), cs_cogs (+1), cs_overheads (+1),
  cs_other (-1); cs_industrial_margin = cs_revenue-cs_cogs; cs_operating_result = cs_industrial_margin
  -cs_overheads; cs_net_income = cs_operating_result+cs_other. default rules: income->cs_revenue,
  income_other->cs_other, expense_direct_cost & expense_depreciation->cs_cogs, expense->cs_overheads.
- ``scheme_pl_contribution`` (base ``cm_revenue``, company level): account cm_revenue (-1), cm_variable (+1),
  cm_fixed (+1), cm_other (-1); cm_margin = cm_revenue-cm_variable; cm_operating_result = cm_margin-cm_fixed;
  cm_net_income = cm_operating_result+cm_other. default rules: cmc_behavior variable->cm_variable,
  fixed & semi_variable->cm_fixed (sequence 50..59); then by type: income->cm_revenue, income_other->cm_other,
  expense / expense_direct_cost / expense_depreciation->cm_fixed.
- ``scheme_cf_functional`` (cash_flow, formulas only; Fazzini 7.4-7.8): cf_gross_self_financing = mon+depreciation;
  cf_net_self_financing = cf_gross_self_financing-taxes; cf_fcoc = cf_net_self_financing-(ccnop-prev_ccnop)
  +(op_liab_nc-prev_op_liab_nc); cf_op_investment = (op_assets_nc-prev_op_assets_nc)+depreciation;
  cf_fco = cf_fcoc-cf_op_investment; cf_ordinary = cf_fco-((fin_assets_nc+fin_assets_c)
  -(prev_fin_assets_nc+prev_fin_assets_c))+fin_income; cf_debt_service = cf_ordinary+extraordinary;
  cf_fcfe = cf_debt_service+(fin_debt-prev_fin_debt)-fin_charges; cf_cash_change = cf_fcfe+(equity-prev_equity)
  -net_income; cf_check = cf_cash_change-(cash-prev_cash).
- ``scheme_cf_oic10`` (cash_flow, OIC 10 indirect): oic_a1 = net_income+taxes+fin_charges-fin_income;
  oic_a2 = oic_a1+non_cash; oic_a3 = oic_a2-(inventory-prev_inventory)-(receivables-prev_receivables)
  +(payables-prev_payables)-(other_op_c-prev_other_op_c)+(other_op_liab_c-prev_other_op_liab_c);
  oic_a = oic_a3-taxes-fin_charges+fin_income+((op_liab_nc-prev_op_liab_nc)-severance-provisions);
  oic_b = -((op_assets_nc-prev_op_assets_nc)+depreciation)-((fin_assets_nc+fin_assets_c)
  -(prev_fin_assets_nc+prev_fin_assets_c)); oic_c = (fin_debt-prev_fin_debt)+(equity-prev_equity)-net_income;
  oic_delta = oic_a+oic_b+oic_c; oic_check = oic_delta-(cash-prev_cash).
- Shipped ratios (company False). liquidity: treasury_margin = assets_c-inventory-liab_c,
  quick_ratio = (assets_c-inventory)/liab_c, nwc = assets_c-liab_c (CCN), current_ratio = assets_c/liab_c,
  dso = 360/(revenue/receivables), dpo = 360/((purchases+services)/payables),
  dio_raw = 360/(raw_consumption/inv_raw) [full_only], dio_finished = 360/(cs_cogs/inv_finished) [full_only],
  commercial_cycle = dso+dio_raw+dio_finished-dpo [full_only], cash_coverage = cash/revenue.
  solidity: autonomy = equity/total_sources, debt_equity = pfn/equity, structure_margin = equity-assets_nc,
  fixed_asset_coverage = equity/assets_nc, interest_coverage = ebitda/fin_charges,
  debt_coverage = ebitda/fin_debt, financing_intensity = fin_debt/revenue, nc_asset_weight = assets_nc/total_assets.
  profitability: roe = net_income/equity, roe_avg = net_income/((prev_equity+equity)/2), roa = mon/total_assets,
  roi = ebit/cin, roi_op = mon/coin, ros = mon/revenue, op_turnover = revenue/coin, rod = fin_charges/fin_debt,
  lev_s = pre_tax/ordinary_result, lev_i = net_income/pre_tax,
  roe_leverage = (roi+(roi-rod)*fin_debt/equity)*lev_s*lev_i, va_per_employee = value_added/employees.
  cash_flow: st_debt_coverage = gross_cash_flow/fin_debt_c. value: ke = rf+beta*(rm-rf),
  wacc = ke*equity/(equity+pfn)+rod*(1-tax_rate)*pfn/(equity+pfn), nopat = mon*(1-tax_rate), eva = nopat-wacc*coin.
  crisis (threshold ``>`` 0 unless noted): crisis_balance = equity+liab_nc-assets_nc, crisis_ccnop = ccnop,
  crisis_autonomy = equity/total_sources (``>=`` 0.5), crisis_mon = mon, crisis_ebit_charges = ebit-fin_charges,
  crisis_spread = roi-rod, crisis_fco_debt = cf_fco-(fin_debt_c+fin_charges).

Extras (WP-13 W1, pinned by ``test_fin_extras.py`` / ``test_fin_report.py``)
- ``cmc.fin.input``: ``models.UniqueIndex`` on (company_id, year, code) -> IntegrityError on a duplicate. An
  input code equal to a line code (or ratio code) of the run is "a code defined twice" -> UserError at compute
  naming the code.
- FIN-06 what-if (area liquidity, ``full_only``): typed inputs ``target_dso``, ``target_dio_raw``,
  ``target_dio_finished``, ``target_dpo``. ``whatif_cycle`` (unit days) = target_dso+target_dio_raw
  +target_dio_finished-target_dpo; ``whatif_cash_release`` (unit amount) = (dso-target_dso)*revenue/360
  +(dio_raw-target_dio_raw)*raw_consumption/360+(dio_finished-target_dio_finished)*cs_cogs/360
  -(dpo-target_dpo)*(purchases+services)/360 (sequence after the ratios they read).
- FIN-09 ``scheme_cf_liquidity`` (cash_flow, formulas only, sequence > ``scheme_cf_oic10``, no base_code):
  liq_generated = cf_gross_self_financing+fin_income+extraordinary; liq_abs_op_c = taxes+(ccnop-prev_ccnop)
  -(op_liab_nc-prev_op_liab_nc); liq_abs_op_nc = cf_op_investment+((fin_assets_nc+fin_assets_c)
  -(prev_fin_assets_nc+prev_fin_assets_c)); liq_abs_debt = fin_charges-(fin_debt-prev_fin_debt);
  liq_abs_equity = net_income-(equity-prev_equity); liq_residual = liq_generated-liq_abs_op_c-liq_abs_op_nc
  -liq_abs_debt-liq_abs_equity (== cf_cash_change); liq_residual_pct = liq_residual/liq_generated*100.
- FIN-10 ``cmc.fin.ratio.area`` gains ``data_quality`` (label "Data quality"); ratios (threshold ``<=`` 0,
  unit ratio except dq_intangible_growth amount): dq_intangible_growth = intangible-prev_intangible;
  dq_inventory_vs_revenue = inventory/prev_inventory-revenue/prev_revenue;
  dq_receivables_vs_revenue = receivables/prev_receivables-revenue/prev_revenue.
- FIN-11 (area value, unit amount): mva_cov = eva/wacc; ev = coin+mva_cov; equity_value = ev-pfn (CEIN = 0).
- FIN-05 ``cmc.fin.criteria.change`` (valuation-criteria change register): ``company_id`` req (default env
  company, check_company, global ir.rule as above), ``year`` Integer req, ``name`` Char req, ``reason`` Text,
  ``line_codes`` Char req = comma-separated line codes (blanks around commas ignored; a code that is not the
  code of an existing scheme line -> ValidationError). ACL user 1111 manager 1111. At compute, value rows of
  the run company whose ``code`` is listed and whose ``year`` = the change year get
  ``comparability_break = True`` (Boolean on ``cmc.fin.analysis.value``): the value of that year AND its YoY
  (year vs year-1) are not comparable. Other years / lines / companies: False.
- ``cmc.fin.analysis._fin_report_data()`` (ensure_one) ->
  ``{"years": [int asc], "schemes": [{"scheme": rec, "lines": [{"code", "name", "line_type", "values": {year: float},
  "common_size": {year: float}, "breaks": [year asc with comparability_break]}]}], "ratios": [{"area": key,
  "area_label": str, "rows": [{"code", "name", "unit", "values": {year: {"value", "status", "benchmark"}}}]}]}``.
  Schemes = the run schemes by (sequence, id), lines by (sequence, id); areas in Selection order, only areas
  with rows; ``benchmark`` float or None. A missing value row = key absent. Query count independent of the
  number of years / rows.
- Report ``cmc_fin_analysis.action_report_cmc_fin_analysis`` (qweb-pdf, model + binding ``cmc.fin.analysis``):
  prints line names, year headers, ``n.c.`` for an absent value, ratio names grouped by area label.

Security (ACL r,w,c,u): scheme / line / rule / ratio user 1000 manager 1111; override / history / input /
benchmark / criteria change user 1111 manager 1111; run user 1110 manager 1111; value / ratio value user 1000 manager 1000.
The run is computed as the calling user: the reader gate applies (group_cmc_user, company in env.companies).

Fixture (company A, calendar fiscal year): 2024 from HISTORY (a GL noise entry in 2024 must be ignored),
2025 and 2026 from the GL (opening entry 2025-01-01, no closing entry for 2025). Hand-computed values:
  year  total_assets equity   liab_nc  liab_c   coin     pfn     revenue    value_added ebitda  mon      net_income
  2024  430,000      120,000  170,000  140,000  250,000  130,000 1,000,000  400,000     150,000  90,000  60,000
  2025  488,000      188,000  155,000  145,000  262,000   74,000 1,100,000  450,000     190,000 126,000  88,000
  2026  557,000      274,000  140,000  143,000  277,000    3,000 1,200,000  503,000     233,000 162,000 116,000
"""

from datetime import date

from odoo import Command
from odoo.tests.common import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

# code -> (name, account_type). No code is a prefix of another (rules match by prefix).
ACCOUNTS = {
    "FXIMM": ("Intangible assets", "asset_non_current"),
    "FXMAT": ("Tangible assets", "asset_fixed"),
    "FXPAR": ("Equity investments", "asset_non_current"),
    "FXRMP": ("Raw materials inventory", "asset_current"),
    "FXRPF": ("Finished goods inventory", "asset_current"),
    "FXCLI": ("Trade receivables", "asset_receivable"),
    "FXALT": ("Prepayments and other receivables", "asset_prepayments"),
    "FXTIT": ("Short-term securities", "asset_current"),
    "FXBNK": ("Bank", "asset_cash"),
    "FXCAP": ("Share capital and reserves", "equity"),
    "FXTFR": ("Severance fund (TFR)", "liability_non_current"),
    "FXMUT": ("Bank loan", "liability_non_current"),
    "FXSOC": ("Interest-free shareholder loan", "liability_non_current"),
    "FXFOR": ("Trade payables", "liability_payable"),
    "FXTRB": ("Tax payables", "liability_current"),
    "FXBCC": ("Bank overdraft", "liability_current"),
    "FXRIC": ("Sales revenue", "income"),
    "FXVPF": ("Change in finished goods", "income_other"),
    "FXACQ": ("Raw material purchases", "expense_direct_cost"),
    "FXVMP": ("Change in raw materials", "expense_direct_cost"),
    "FXSER": ("Services", "expense"),
    "FXPER": ("Wages and salaries", "expense"),
    "FXCTF": ("Severance accrual", "expense"),
    "FXAMM": ("Depreciation", "expense_depreciation"),
    "FXPFI": ("Financial income", "income_other"),
    "FXOFI": ("Interest expense", "expense"),
    "FXIMP": ("Income taxes", "expense"),
}

# natural sign (debit +): pre-closing trial balance 2024, imported as history (sums to 0)
HISTORY_2024 = {
    "FXIMM": 10000, "FXMAT": 200000, "FXPAR": 20000, "FXRMP": 30000, "FXRPF": 20000, "FXCLI": 120000,
    "FXALT": 10000, "FXTIT": 5000, "FXBNK": 15000,
    "FXCAP": -60000, "FXTFR": -40000, "FXMUT": -100000, "FXSOC": -30000, "FXFOR": -80000, "FXTRB": -20000,
    "FXBCC": -40000,
    "FXRIC": -1000000, "FXACQ": 400000, "FXSER": 200000, "FXPER": 250000, "FXCTF": 20000, "FXAMM": 40000,
    "FXPFI": -1000, "FXOFI": 11000, "FXIMP": 20000,
}
# 2024 closing balances with the 2024 result closed into equity (120,000 = 60,000 + 60,000)
OPENING_2025 = {
    "FXIMM": 10000, "FXMAT": 200000, "FXPAR": 20000, "FXRMP": 30000, "FXRPF": 20000, "FXCLI": 120000,
    "FXALT": 10000, "FXTIT": 5000, "FXBNK": 15000,
    "FXCAP": -120000, "FXTFR": -40000, "FXMUT": -100000, "FXSOC": -30000, "FXFOR": -80000, "FXTRB": -20000,
    "FXBCC": -40000,
}
# 2025: result 88,000, dividends 20,000 (debit FXCAP)
MOVES_2025 = {
    "FXRIC": -1100000, "FXVPF": -4000, "FXACQ": 450000, "FXVMP": -6000, "FXSER": 210000, "FXPER": 260000,
    "FXCTF": 22000, "FXAMM": 42000, "FXPFI": -1000, "FXOFI": 9000, "FXIMP": 30000,
    "FXIMM": -2000, "FXMAT": 10000, "FXRMP": 6000, "FXRPF": 4000, "FXCLI": 12000, "FXALT": 2000,
    "FXBNK": 26000, "FXCAP": 20000, "FXTFR": -5000, "FXMUT": 20000, "FXFOR": -10000, "FXTRB": -5000,
    "FXBCC": 10000,
}
# 2026: result 116,000, dividends 30,000
MOVES_2026 = {
    "FXRIC": -1200000, "FXVPF": -6000, "FXACQ": 480000, "FXVMP": 3000, "FXSER": 220000, "FXPER": 270000,
    "FXCTF": 24000, "FXAMM": 47000, "FXPFI": -2000, "FXOFI": 8000, "FXIMP": 40000,
    "FXIMM": -2000, "FXMAT": 10000, "FXPAR": 5000, "FXRMP": -3000, "FXRPF": 6000, "FXCLI": 18000,
    "FXALT": -1000, "FXBNK": 36000, "FXCAP": 30000, "FXTFR": -5000, "FXMUT": 20000, "FXFOR": -5000,
    "FXTRB": -3000, "FXBCC": 10000,
}

# company rules (sequence 10, before the shipped defaults) for the accounts the defaults cannot place
RULES = {
    "scheme_bs_functional": {
        "FXIMM": "intangible", "FXPAR": "fin_assets_nc", "FXRMP": "inv_raw", "FXRPF": "inv_finished",
        "FXTIT": "fin_assets_c", "FXTFR": "op_liab_nc", "FXBCC": "fin_debt_c",
    },
    "scheme_pl_value_added": {
        "FXVPF": "fg_inv_change", "FXVMP": "raw_inv_change", "FXPER": "personnel", "FXCTF": "severance",
        "FXPFI": "fin_income", "FXOFI": "fin_charges", "FXIMP": "taxes",
    },
    "scheme_pl_cost_of_sales": {
        "FXVPF": "cs_cogs", "FXPER": "cs_cogs", "FXCTF": "cs_cogs",
        "FXPFI": "cs_other", "FXOFI": "cs_other", "FXIMP": "cs_other",
    },
    "scheme_pl_contribution": {
        "FXVPF": "cm_revenue", "FXPFI": "cm_other", "FXOFI": "cm_other", "FXIMP": "cm_other",
    },
}

SCHEME_XMLIDS = (
    "scheme_bs_functional", "scheme_pl_value_added", "scheme_pl_cost_of_sales",
    "scheme_pl_contribution", "scheme_cf_functional", "scheme_cf_oic10",
)


class FinAnalysisCommon(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; schemes are configured by a CMC manager
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company_data_b = cls.setup_other_company(name="FIN analysis B")
        cls.company_b = cls.company_data_b["company"]
        cls.acc = {}
        Account = cls.env["account.account"].with_company(cls.company)
        for code, (name, account_type) in ACCOUNTS.items():
            cls.acc[code] = Account.create({
                "code": code, "name": name, "account_type": account_type,
                "company_ids": [Command.set(cls.company.ids)],
            })
        cls.schemes = {xid: cls.env.ref("cmc_fin_analysis.%s" % xid) for xid in SCHEME_XMLIDS}
        rules = []
        for xid, mapping in RULES.items():
            scheme = cls.schemes[xid]
            rules += [{
                "scheme_id": scheme.id, "company_id": cls.company.id, "sequence": 10,
                "account_code_prefix": code, "line_id": cls._line(scheme, line_code).id,
            } for code, line_code in mapping.items()]
        cls.env["cmc.fin.mapping.rule"].create(rules)
        # contribution P&L: the behavior comes from the cmc_account classification rules (AD-116)
        cls.env["cmc.classification.rule"].create([
            {"name": "Materials variable", "account_code_prefix": "FXACQ", "cmc_behavior": "variable", "company_id": cls.company.id},
            {"name": "Raw change variable", "account_code_prefix": "FXVMP", "cmc_behavior": "variable", "company_id": cls.company.id},
            {"name": "Services fixed", "account_code_prefix": "FXSER", "cmc_behavior": "fixed", "company_id": cls.company.id},
        ])
        cls.env["cmc.fin.history"].create([
            {"company_id": cls.company.id, "year": 2024, "account_id": cls.acc[code].id, "balance": balance}
            for code, balance in HISTORY_2024.items()
        ])
        # GL noise in a history year: ignored for 2024 (history wins); P&L only, so equity is unaffected
        cls._entry(date(2024, 5, 31), {"FXSER": 999, "FXRIC": -999})
        cls._entry(date(2025, 1, 1), OPENING_2025)
        cls._entry(date(2025, 6, 30), MOVES_2025)
        cls._entry(date(2026, 6, 30), MOVES_2026)
        cls.env["cmc.fin.input"].create([
            {"company_id": cls.company.id, "year": 2026, "code": code, "value": value}
            for code, value in (("rf", 0.03), ("beta", 1.2), ("rm", 0.08), ("tax_rate", 0.24), ("employees", 10))
        ])
        cls.env["cmc.fin.benchmark"].create({
            "company_id": cls.company.id, "ateco_code": "25.62", "year": 2026,
            "ratio_id": cls._ratio_def("roe").id, "value": 0.15,
        })
        cls.cmc_user = new_test_user(
            cls.env, login="fin_cmc_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.cmc_user_b = new_test_user(
            cls.env, login="fin_cmc_user_b", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="fin_outsider", groups="base.group_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    # ---- helpers ------------------------------------------------------------------------------

    @classmethod
    def _line(cls, scheme, code):
        line = scheme.line_ids.filtered(lambda line: line.code == code)
        assert len(line) == 1, "scheme %s has no single line %s" % (scheme.code, code)
        return line

    @classmethod
    def _ratio_def(cls, code):
        return cls.env["cmc.fin.ratio"].search([("code", "=", code), ("company_id", "=", False)], limit=1)

    @classmethod
    def _entry(cls, day, amounts):
        move = cls.env["account.move"].with_company(cls.company).create({
            "move_type": "entry",
            "date": day,
            "journal_id": cls.company_data["default_journal_misc"].id,
            "line_ids": [
                Command.create({
                    "name": code, "account_id": cls.acc[code].id,
                    "debit": amount if amount > 0 else 0.0, "credit": -amount if amount < 0 else 0.0,
                })
                for code, amount in amounts.items() if amount
            ],
        })
        move.action_post()
        return move

    @classmethod
    def _run(cls, year_from=2024, year_to=2026, user=None, **vals):
        Run = cls.env["cmc.fin.analysis"]
        if user:
            Run = Run.with_user(user)
        return Run.create({
            "name": "Analysis %s-%s" % (year_from, year_to),
            "company_id": cls.company.id, "year_from": year_from, "year_to": year_to, **vals,
        })

    def _row(self, run, code, year):
        rows = run.value_ids.filtered(lambda row: row.code == code and row.year == year)
        self.assertEqual(len(rows), 1, "expected one value row for %s/%s" % (code, year))
        return rows

    def _val(self, run, code, year):
        return self._row(run, code, year).value

    def _ratio(self, run, code, year):
        rows = run.ratio_value_ids.filtered(lambda row: row.code == code and row.year == year)
        self.assertEqual(len(rows), 1, "expected one ratio row for %s/%s" % (code, year))
        return rows

    def assertValues(self, run, year, expected):
        for code, value in expected.items():
            self.assertAlmostEqual(self._val(run, code, year), value, places=2, msg="%s %s" % (code, year))

    def assertRatios(self, run, year, expected, places=6):
        for code, value in expected.items():
            row = self._ratio(run, code, year)
            self.assertNotEqual(row.status, "not_computable", "%s %s" % (code, year))
            self.assertAlmostEqual(row.value, value, places=places, msg="%s %s" % (code, year))
