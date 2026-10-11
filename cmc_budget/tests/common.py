"""WP-02 ``cmc_budget`` - master/flexible budget, forecast, variances, GPK closing, cash forecast.

Capabilities: BUD-01, BUD-02, BUD-03, BUD-04, FCS-01, FCS-04, STD-02, STD-03, CAP-04, ALC-06, INT-07.
Binding decisions: AD-102 (depends ``cmc_account`` only), AD-104 (plan lines live on a ``cmc.plan.version``
of type budget/forecast, locked by the run mixin), AD-105 (month grain), AD-107/108 (planned quantities are
``cmc.measure.quantity`` facts with ``version_id``; no parallel quantity table), AD-110 (never analytic lines),
AD-111 (costs through the analytic cost reader, revenue through the ``cmc_account`` GL reader), AD-112 (stored
natural sign: cost < 0, revenue > 0; variance = actual - plan, positive = favourable), AD-113 (engines are
runs, snapshots, AD-002 usage on the version), AD-114 (BvA = live SQL view, grouped reads), AD-115 (groups).

CONTRACT
========
Monetary convention: every ``amount``-like field below is NATURAL sign (cost negative, revenue positive) unless
called ``figure``/``rate``/``qty``. Variances = actual - reference: positive = favourable.

1. ``cmc.budget.seasonality`` (manager r/w/c/u, user read): ``name``, ``company_id`` (required, default
   env.company), ``line_ids`` O2m ``cmc.budget.seasonality.line`` (``seasonality_id``, ``month`` Integer 1..12,
   ``weight`` Float >= 0; month outside 1..12 or twice in one profile -> ValidationError).
   ``_cmc_weights() -> {month number: weight}``.

2. ``cmc.budget.line`` (``_inherit = ["cmc.month.mixin", "cmc.run.line.mixin"]``, ``_cmc_run_field = "version_id"``,
   so an APPROVED version refuses create/write/unlink of its lines with UserError):
   ``version_id`` (required, cascade), ``company_id`` (related version, stored), ``currency_id`` (related version
   currency), ``company_currency_id``; ``kind`` Selection ``cost`` (default) / ``revenue``; ``center_id``
   (account.analytic.account, check_company; REQUIRED for cost lines -> ValidationError); ``element_id``
   (cmc.cost.element, check_company: RN-003); ``behavior`` Selection ``fixed`` (default) / ``variable``;
   ``driver_id`` (cmc.cost.driver, the pool/driver of the line, check_company: RN-003) - a line of company B with
   an element or driver of company A is refused (UserError/ValidationError); company-less master data is shared; ``responsibility_id`` (cmc.responsibility.center, area); ``business_line_id``
   (account.analytic.account, any plan); ``payment_term_id`` (override for FCS-04);
   ``figure`` Monetary = the positive figure as typed (compute/inverse of ``amount``);
   ``amount`` Monetary = natural sign in VERSION currency (cost: -figure, revenue: +figure);
   ``amount_company`` Monetary stored = ``version._cmc_to_company_currency(amount)`` (INT-07; follows a change of
   ``currency_rate`` on a draft version). ``date`` outside the version span -> ValidationError.

3. ``cmc.plan.version`` (``_inherit`` in cmc_budget): ``budget_line_ids`` O2m (copy=True, so the base ``copy()``
   duplicates them: budget -> forecast by ``copy({"version_type": "forecast"})``); ``target_total_cost``
   Monetary (positive, 0 = no check); ``target_profit`` Monetary; ``alert_threshold`` Float percent (default 10.0).
   The responsible group gets READ on cmc.plan.version.
   - ``_cmc_budget_add(vals, total, seasonality=None) -> cmc.budget.line``: one line per month of the version span
     with the ``vals`` keys, ``figure`` phased so the parts re-sum to ``total`` (``cmc_utils.phase_by_month``,
     currency rounding). Fixed lines: equal per month (seasonality ignored, BUD-02 fixed = annual/12). Variable and
     revenue lines: ``seasonality._cmc_weights()`` if given, else, when ``vals`` has a ``driver_id``, the planned
     quantities of the version (facts ``version_id`` = self, driver, ``center_id``) per month, else equal.
   - ``_cmc_center_budget(date_from=None, date_to=None) -> {center id: {"fixed", "variable", "total"}}``:
     positive cost figures in company currency (BUD-01: center budget = fixed + var rate x planned driver).
   - ``_cmc_variable_rate(center, driver, date_from=None, date_to=None) -> float``: variable cost figures of the
     center on that driver / planned quantity of the version for (driver, center); 0 without quantity.
   - ``action_approve()`` (base, manager only): UserError when ``target_total_cost`` is set and differs from the
     sum of the cost figures (company total consistency, BUD-01).
   - ``_cmc_rolling_forecast(cutoff) -> cmc.plan.version`` (FCS-01): NEW draft version, type ``forecast``,
     ``parent_id`` = self, same span/currency. Months <= month of ``cutoff``: the ACTUALS (analytic cost per
     cmc center x element x behavior, read with the cost reader in the company perimeter; revenue from
     ``account.move.line._cmc_revenue_read_group`` per month, kind revenue, no center); the budget lines of
     those months are NOT copied. Months after the cutoff: copy of the budget lines (the revised remainder,
     editable while draft). forecast = actual_1..m + revised_m+1..12.
     RN-001 (INT-07): actuals are company currency; the actual lines are written back in VERSION currency,
     ``figure = actual / currency_rate`` (version currency rounding), so their ``amount_company`` equals the
     actual (company currency rounding) and the forecast BvA variance of an actual month is 0 - costs and revenue.
     Semi-variable actuals (T-B, dispatch C3): split with the ``cmc.cost.function`` of center x element
     (fallback: the function of the center with ``element_id`` False; the element-specific one wins):
     variable = ``variable_rate`` x Q, Q = ACTUAL facts (``version_id`` False) of the function's
     ``driver_id`` on the center in that month; fixed = actual - variable (K is not used). No function, a
     ``step`` function or Q = 0 -> the whole actual is fixed. Actual fixed/variable lines keep their behavior.
     Batched: one facts read and one function search per forecast (query count independent of volume).
   - ``action_split_annual() -> cmc.plan.version`` (BUD-03): one draft budget version per calendar year of a
     multi-year span (name contains the year, ``parent_id`` = self, same currency/rate) with that year's lines.
     RN-002: also the planned ``cmc.measure.quantity`` facts (``version_id`` = self) of each year are copied to the
     matching child (``version_id`` = child), so flexible budget / variances of a child equal the parent's year.
   - ``_cmc_profit_gap() -> {"revenue", "cost", "profit", "target", "gap"}``: positive company-currency figures,
     profit = revenue - cost, gap = target_profit - profit.
   - ``_cmc_bva_summary(date_to, groupby=("center_id",)) -> list[dict]`` (BUD-04), company currency, natural
     sign; one dict per group with the groupby keys (records, False when empty) and ``plan_month``,
     ``actual_month``, ``variance_month``, ``variance_month_pct``, ``plan_ytd``, ``actual_ytd``, ``variance_ytd``,
     ``variance_ytd_pct``, ``prior_ytd`` (actual of the same months one year earlier), ``alert_month``,
     ``alert_ytd``. month = month of ``date_to``; YTD = Jan 1 of that year (or version start if later) .. end of
     that month; pct = variance / abs(plan) x 100 (0 when plan is 0); alert = abs(pct) > ``alert_threshold``.
     Grouped reads only: query count independent of lines/months (AD-114).
     T-A (BUD-01): ``groupby=("business_line_id",)`` groups by the line's business line; actuals carry no
     business line (the cost reader is center based) and fall in the ``False`` group.
     FX-009 / DL-004: output keys, natural sign and T-A semantics UNCHANGED (the summary is NOT the view's
     presentation sign); only APPROVED versions are compared: on a draft version it returns ``[]``.

4. ``cmc.budget.bva`` (``_auto = False`` SQL view, read-only, AD-114 (7); FX-009 / DL-004 / DL-006): ``company_id``,
   ``version_id``, ``version_type``, ``is_latest_version``, ``date`` (month), ``kind``, ``center_id``,
   ``element_id``, ``behavior``, ``business_line_id``, ``plan_amount``, ``actual_amount``, ``variance``,
   ``prior_actual_amount`` (actual of the same key 12 months earlier), company currency, ``aggregator="sum"``.
   Only APPROVED (``state = 'done'``) versions of type budget/forecast have rows: a draft version is never in the
   view. Row key = version x month x kind x center x element (ONE row: plan and actual of the same key are never
   split apart). ``behavior`` and ``business_line_id`` are informational: the single non-null value carried by the
   row's plan lines, False when the plan lines carry several (or none); actuals never contribute to them.
   PRESENTATION sign (DL-006): ``plan_amount`` / ``actual_amount`` / ``prior_actual_amount`` are POSITIVE figures
   for costs (= -natural) and for revenue; ``variance`` = natural actual - natural plan (positive = favourable:
   a cost of 900 against a plan of 1,000 is +100). Each approved version joins the actuals once (grouped by version
   the actual shows once per version, never multiplied). ``is_latest_version`` marks the latest approved version;
   the menu action ``cmc_budget.action_cmc_budget_bva`` opens with ``search_default_latest_version`` in its
   context. ``is_alert`` (RN-004) = abs(variance) / abs(plan) x 100 > ``alert_threshold`` of the version on the
   row (plan 0 -> False). One row per approved version x month of its span x key present in plan, actual or prior
   actual. Actual cost = analytic lines (``cmc_excluded`` False) of the company on cmc centers, cost element;
   actual revenue = posted income journal items (kind revenue, no center). LIVE: a new posting shows without any
   recompute.

5. ``cmc.budget.variance.run`` (cmc.run.mixin; STD-02, STD-03, CAP-04): ``name``, ``company_id``, ``version_id``
   (budget/forecast, check_company), ``date_from``, ``date_to``, ``input_ids`` O2m ``cmc.budget.variance.input``
   (run-line-mixin: ``run_id``, ``center_id``, ``driver_id``, ``allowed_qty`` = standard quantity allowed for the
   actual output; missing -> allowed = actual qty), ``line_ids`` results ``cmc.budget.variance.line``.
   Compute: version must be APPROVED (UserError otherwise); RN-002: a variable line in scope whose (center,
   driver) has no planned quantity in the version -> UserError (no silent fallback to the static budget); its usage is recorded (AD-002: the version cannot
   be reset while the run is computed). Snapshot: a later posting changes nothing until reset + compute.
   Scope = centers with budget lines in the version in the span. One result line per center x element:
   ``center_id``, ``element_id``, ``driver_id``, ``planned_qty`` (version facts), ``actual_qty`` (actual facts,
   ``version_id`` False), ``allowed_qty``, ``budget_fixed``, ``budget_variable`` (static budget), ``flexible_amount``,
   ``actual_amount`` (cost reader by element), ``spending_variance``, ``efficiency_variance``,
   ``volume_variance``, ``practical_qty``, ``capacity_rate``, ``capacity_assigned``, ``capacity_unused_budgeted``,
   ``capacity_unused_unexpected``. With v = -budget_variable / planned_qty, f = -budget_fixed / planned_qty:
     flexible_amount     = budget_fixed - v x actual_qty            (K + v.Q_actual, natural sign)
     spending_variance   = actual_amount - flexible_amount
     efficiency_variance = v x (allowed_qty - actual_qty)
     volume_variance     = f x (allowed_qty - planned_qty)          (fixed lines with a driver, else 0)
     => actual_amount + (v + f) x allowed_qty = spending + efficiency + volume
   Capacity (fixed lines; practical = ``cmc.capacity.analysis.capacity_practical`` of center x driver for the
   span; all 0 without it): capacity_rate = -budget_fixed / practical_qty; capacity_assigned = -rate x actual;
   capacity_unused_budgeted = -rate x (practical - planned); capacity_unused_unexpected = -rate x (planned - actual);
   assigned + unused_budgeted + unused_unexpected = budget_fixed.

6. ``cmc.closing.run`` (``_inherit`` in cmc_budget, ALC-06): ``allocation_basis`` Selection ``actual`` (default,
   unchanged behaviour) / ``dual_rate``; ``gpk_version_id`` (approved budget/forecast version); both inputs.
   dual_rate (method direct): for each provider with budget lines in the version over the span, per pool =
   ``driver_id`` of its lines: fixed charge to receiver R = fixed figures x planned_R / sum(planned) (planned =
   version facts driver x center R); variable charge = (variable figures / sum(planned)) x actual service
   quantity (``cmc.center.service`` provider -> R with that driver). ``cost_distributed`` = sum of charges; the
   difference to the actual cost STAYS on the provider (``cost_residual``, never pushed). Providers without lines
   in the version keep the actual basis. Draft version -> UserError; usage recorded (AD-002).

7. ``cmc.budget.cash.run`` (cmc.run.mixin, FCS-04): ``name``, ``company_id``, ``version_id`` (approved),
   ``opening_balance``, ``customer_payment_term_id``, ``supplier_payment_term_id``; results ``line_ids``
   ``cmc.budget.cash.line`` (``date`` month, ``inflow`` >= 0, ``outflow`` <= 0, ``net`` = inflow + outflow,
   ``cumulative`` = opening + running net). Each budget line (amount_company) is invoiced on the 15th of its
   month; term = line ``payment_term_id`` or the run's customer (revenue) / supplier (cost) term, none = same
   month; each percent term line moves its share to the month of ``term_line._get_due_date(15th)``. Cost lines
   whose element has ``cmc_cash_excluded`` (new Boolean on ``cmc.cost.element``, e.g. depreciation) or is
   figurative are no cash. Lines cover every month from the version start to the last due month.

8. Security (AD-115): every model has ``company_id`` + global rule ``company_id parent_of company_ids`` (not
   noupdate). ``group_cmc_user``: r/w/c/u on budget lines and draft versions, creates/computes runs (results
   read-only); ``group_cmc_manager``: approves/resets; ``group_cmc_responsible``: READ only on
   ``cmc.budget.line`` and ``cmc.budget.bva``, rule ``['|', ('center_id.cmc_responsibility_ids.owner_id', '=',
   user.id), ('responsibility_id.owner_id', '=', user.id)]``, user group unrestricted rule; no access to runs.
   Outsider (base.group_user): AccessError.
"""

from datetime import date

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user

Y = 2033


def d(month, day=1, year=Y):
    return date(year, month, day)


class CmcBudgetCommon:
    """Fixture helpers shared by the TransactionCase and AccountTestInvoicingCommon based classes."""

    @classmethod
    def _cmc_budget_setup(cls, company=None):
        cls.company = company or cls.env.company
        cls.Version = cls.env["cmc.plan.version"]
        cls.Line = cls.env["cmc.budget.line"]
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Qty = cls.env["cmc.measure.quantity"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.manager = new_test_user(
            cls.env, login=f"bud_mgr_{cls.company.id}", groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        # RN-003: element and driver are company-checked on the budget line, so the fixture binds them to the company
        Element = cls.env["cmc.cost.element"]
        cls.rent = Element.create({"name": "BUD Rent", "code": "BUDRENT", "company_id": cls.company.id})
        cls.energy = Element.create({"name": "BUD Energy", "code": "BUDNRG", "company_id": cls.company.id})
        cls.labour = Element.create({"name": "BUD Labour", "code": "BUDLAB", "company_id": cls.company.id})
        Driver = cls.env["cmc.cost.driver"]
        cls.mh = Driver.create({"name": "BUD machine hours", "code": "BUDMH", "company_id": cls.company.id})
        cls.lh = Driver.create({"name": "BUD labour hours", "code": "BUDLH", "company_id": cls.company.id})

    @classmethod
    def _center(cls, name, center_type="final", company=None):
        return cls.AA.create({
            "name": name, "plan_id": cls.plan.id, "cmc_center_type": center_type,
            "company_id": (company or cls.company).id,
        })

    @classmethod
    def _version(cls, name="BUD 2033", date_from=None, date_to=None, company=None, **vals):
        return cls.Version.create({
            "name": name, "version_type": "budget", "company_id": (company or cls.company).id,
            "date_from": date_from or d(1), "date_to": date_to or d(12, 31), **vals,
        })

    @classmethod
    def _line(cls, version, center, month, figure, element=None, behavior="fixed", kind="cost", **vals):
        return cls.Line.create({
            "version_id": version.id, "kind": kind, "center_id": center.id if center else False,
            "element_id": (element or cls.rent).id, "behavior": behavior, "date": d(month),
            "figure": figure, **vals,
        })

    @classmethod
    def _cost(cls, center, amount, day, element=None, behavior="fixed"):
        """An actual cost (positive figure) booked on the center: analytic amount = -amount (AD-005)."""
        return cls.AL.create({
            "name": "BUD actual", "account_id": center.id, "amount": -amount, "date": day,
            "cmc_cost_element_id": (element or cls.rent).id, "cmc_behavior": behavior,
        })

    @classmethod
    def _qty(cls, driver, center, month, qty, version=None, year=Y):
        return cls.Qty.create({
            "driver_id": driver.id, "center_id": center.id, "date": d(month, year=year), "quantity": qty,
            "version_id": version.id if version else False, "company_id": center.company_id.id or cls.company.id,
        })

    @classmethod
    def _foreign(cls, code="CHF"):
        """An active currency other than the company one (RN-001: CHF version on a USD company)."""
        currency = cls.env.ref(f"base.{code}")
        if currency == cls.company.currency_id:
            currency = cls.env.ref("base.EUR")
        currency.active = True
        return currency

    def _approve(self, version):
        version.with_user(self.manager).action_approve()
        return version


class CmcBudgetCase(CmcBudgetCommon, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cmc_budget_setup()
