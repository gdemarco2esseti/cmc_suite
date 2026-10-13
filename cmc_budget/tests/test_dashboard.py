"""DASH (controller dashboard) - contribution of ``cmc_budget`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (budget), UX-002 (KPI 2, "Da verificare" budget row, chart 2), DASH-ADR.
- One ``_read_group`` on ``cmc.budget.bva`` (is_latest_version, date in period, company) grouped ``date:month``
  (``date:year`` for kind "all"), sum plan_amount / actual_amount -> KPI + card + chart.
- KPI ``budget.variance`` (slot "budget", NO fallback): raw = (actual - plan) / plan (presentation sign: cost figures
  positive); actual above plan = unfavourable. Never an approved budget version -> KPI not emitted; data but no row
  in the period -> raw None.
- Card ``budget.bva`` (planning, 10), action ``cmc_budget.action_cmc_budget_bva``; no approved version -> "empty".
- Alert ``budget.over_threshold`` (warning, scope records): search_count is_alert = True on the same domain; invariant
  search_count(action domain AND alert domain) == count.
- Chart ``budget.bva_by_month`` (bar): one label per month with rows for month/quarter/year, absent for "month",
  grouped by year for "all".

Hand numbers (company currency, cost center C, element rent, threshold 10 %): plan Jan/Feb/Mar 1,000 each;
actual Jan 900 (-10 %, not flagged: 10 is not > 10), Feb 1,200 (+20 %, flagged), Mar 1,050 (+5 %).
Year 2026: plan 3,000, actual 3,150 -> raw 5.0 (%) unfavourable; January alone: raw -10.0 (%) favourable.
The view marks the latest approved version with CURRENT_DATE: the test company has a single approved version.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import format_date
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

KPI = "budget.variance"
CARD = "budget.bva"
ALERT = "budget.over_threshold"
CHART = "budget.bva_by_month"
BVA_ACTION = "cmc_budget.action_cmc_budget_bva"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_budget_user"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "DASH company %s" % cls._login})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env, login=cls._login, groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    @classmethod
    def _done(cls, record, **vals):
        record.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done", **vals})
        return record

    def _data(self, kind="year", ref="2026-06-15", user=None):
        cockpit = self.env["cmc.cockpit"]
        if user:
            cockpit = cockpit.with_user(user)
        return cockpit.get_dashboard_data({"kind": kind, "ref": ref})

    @staticmethod
    def _item(data, section, key):
        return next((item for item in data[section] if item["key"] == key), None)

    def _keys(self, data, section, prefix):
        return [item["key"] for item in data[section] if item["key"].startswith(prefix)]

    def assertRawIn(self, value, item):
        raws = [f["raw"] for f in item["figures"]]
        self.assertTrue(
            any(isinstance(r, (int, float)) and not isinstance(r, bool) and abs(r - value) < 0.005 for r in raws),
            "%s not among the figures %s of %s" % (value, raws, item["key"]),
        )

    def _assert_records_invariant(self, alert):
        # AD-002: the deep link opens action domain AND alert domain; it must select exactly the counted records
        action = self.env["ir.actions.actions"]._for_xml_id(alert["action"])
        action_domain = action.get("domain") or []
        if isinstance(action_domain, str):
            action_domain = safe_eval(action_domain, {"uid": self.env.uid, "context": {}})
        domain = Domain([tuple(t) if isinstance(t, list) else t for t in action_domain]) & Domain(
            [tuple(t) if isinstance(t, list) else t for t in alert["domain"]]
        )
        self.assertEqual(self.env[action["res_model"]].search_count(list(domain)), alert["count"], alert)

    def _revoke_read(self, *models):
        self.env["ir.model.access"].search([("model_id.model", "in", list(models))]).write({"perm_read": False})


@tagged("post_install", "-at_install")
class TestBudgetDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login="dash_budget_manager", groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.version, cls.center, cls.rent = cls._budget(cls.company)

    @classmethod
    def _budget(cls, company):
        """Draft 2026 budget of `company` with plan Jan-Mar 1,000 and the actuals 900 / 1,200 / 1,050."""
        env = cls.env(context=dict(cls.env.context, allowed_company_ids=[company.id]))
        rent = env["cmc.cost.element"].create({"name": "DASH rent", "code": "DASHRENT%s" % company.id, "company_id": company.id})
        center = env["account.analytic.account"].create({
            "name": "DASH center", "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": company.id,
        })
        version = env["cmc.plan.version"].create({
            "name": "DASH budget 2026", "version_type": "budget", "company_id": company.id,
            "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        env["cmc.budget.line"].create([{
            "version_id": version.id, "kind": "cost", "center_id": center.id, "element_id": rent.id, "behavior": "fixed",
            "date": "2026-%02d-01" % month, "figure": 1000.0,
        } for month in (1, 2, 3)])
        env["account.analytic.line"].create([{
            "name": "DASH actual", "account_id": center.id, "amount": -amount, "date": day, "company_id": company.id,
            "cmc_cost_element_id": rent.id, "cmc_behavior": "fixed",
        } for day, amount in (("2026-01-10", 900.0), ("2026-02-10", 1200.0), ("2026-03-10", 1050.0))])
        return version, center, rent

    def _approve(self, version=None):
        (version or self.version).with_user(self.manager).action_approve()
        self.env.flush_all()

    def test_no_approved_version_empty_card_and_no_kpi(self):
        # UX-002: a draft version is never budget data -> empty card, no KPI tile, no alert, no chart
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "planning")
        self.assertEqual(card["action"], BVA_ACTION)
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))
        self.assertIsNone(self._item(data, "charts", CHART))

    def test_variance_kpi_unfavourable_when_actual_above_plan(self):
        # UX-002 KPI 2: (actual - plan) / plan over the latest approved version in the period; overspend = unfavourable
        self._approve()
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertEqual(kpi["slot"], "budget")
        self.assertAlmostEqual(kpi["raw"], (3150.0 - 3000.0) / 3000.0 * 100, places=2)  # percent units (R18)
        self.assertTrue(kpi["trend"])
        self.assertFalse(kpi["trend"]["favourable"])

    def test_variance_kpi_uses_cost_rows_only(self):
        # RN-001: a revenue plan line and a posted income entry in the period must not net against the cost variance
        env, company = self.env, self.company
        env["cmc.budget.line"].create([{
            "version_id": self.version.id, "kind": "revenue", "center_id": self.center.id, "date": "2026-%02d-01" % month,
            "figure": 5000.0,
        } for month in (1, 2, 3)])
        income = env["account.account"].create({
            "name": "DASH income", "code": "DASH700", "account_type": "income", "company_ids": [Command.set(company.ids)]})
        other = env["account.account"].create({
            "name": "DASH asset", "code": "DASH100", "account_type": "asset_current", "company_ids": [Command.set(company.ids)]})
        journal = env["account.journal"].create({
            "name": "DASH journal", "code": "DSHJ", "type": "general", "company_id": company.id})
        env["account.move"].create({
            "journal_id": journal.id, "date": "2026-02-15", "company_id": company.id,
            "line_ids": [Command.create({"account_id": other.id, "debit": 2000.0, "credit": 0.0}),
                         Command.create({"account_id": income.id, "debit": 0.0, "credit": 2000.0})],
        }).action_post()
        self._approve()
        data = self._data()
        self.assertAlmostEqual(self._item(data, "kpis", KPI)["raw"], 5.0, places=2)
        card = self._item(data, "cards", CARD)
        self.assertRawIn(3000.0, card)
        self.assertRawIn(3150.0, card)
        self.assertEqual(self._item(data, "charts", CHART)["datasets"][0]["data"], [1000.0, 1000.0, 1000.0])

    def test_running_period_compares_closed_months_only(self):
        # RN-003: mid-October, the October plan row and an actual dated after today must not enter the comparison
        self.env["cmc.budget.line"].create({
            "version_id": self.version.id, "kind": "cost", "center_id": self.center.id, "element_id": self.rent.id,
            "behavior": "fixed", "date": "2026-10-01", "figure": 1000.0})
        self.env["account.analytic.line"].create({
            "name": "DASH late", "account_id": self.center.id, "amount": -5000.0, "date": "2026-10-20",
            "company_id": self.company.id, "cmc_cost_element_id": self.rent.id, "cmc_behavior": "fixed"})
        self._approve()
        with _freeze_time("2026-10-13 12:00:00"):
            data = self._data(ref="2026-10-13")
        kpi = self._item(data, "kpis", KPI)
        self.assertAlmostEqual(kpi["raw"], 5.0, places=2)
        # month label is localised (en_US may not even be installed): compute it the same way
        sep = format_date(self.env, date(2026, 9, 1), date_format="MMM yyyy")
        self.assertIn(sep, kpi["caption"])
        card = self._item(data, "cards", CARD)
        self.assertRawIn(3000.0, card)
        self.assertIn(sep, card["subtitle"])
        self.assertEqual(self._item(data, "charts", CHART)["labels"], [format_date(self.env, date(2026, m, 1), date_format="MMM yyyy") for m in (1, 2, 3)])
        self.assertTrue(kpi["trend"]["text"].startswith("Unfavourable"))

    def test_running_month_without_closed_month_shows_dash(self):
        self._approve()
        with _freeze_time("2026-03-13 12:00:00"):
            data = self._data(kind="month", ref="2026-03-13")
        kpi = self._item(data, "kpis", KPI)
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["sub"], "Current month not closed yet")
        self.assertIsNone(self._item(data, "alerts", ALERT))
        self.assertIsNone(self._item(data, "charts", CHART))
        self.assertTrue(self._item(data, "cards", CARD))

    def test_variance_kpi_favourable_when_actual_below_plan(self):
        # UX-002 KPI 2: January spends 900 against 1,000 -> -10 %, favourable
        self._approve()
        kpi = self._item(self._data(kind="month", ref="2026-01-15"), "kpis", KPI)
        self.assertAlmostEqual(kpi["raw"], (900.0 - 1000.0) / 1000.0 * 100, places=2)  # percent units (R18)
        self.assertTrue(kpi["trend"]["favourable"])

    def test_variance_kpi_dash_when_no_row_in_period(self):
        # UX-002: approved budget exists but no row in the period -> raw None, "—"; no fallback to another source
        self._approve()
        kpi = self._item(self._data(ref="2025-06-15"), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")
        self.assertEqual(kpi["slot"], "budget")

    def test_card_figures_actual_and_plan(self):
        # DASH 2: the card bullet = actual vs plan of the period (raw == view sums)
        self._approve()
        [(plan, actual)] = self.env["cmc.budget.bva"]._read_group(
            [("company_id", "=", self.company.id), ("is_latest_version", "=", True)], [], ["plan_amount:sum", "actual_amount:sum"],
        )
        self.assertAlmostEqual(plan, 3000.0)
        self.assertAlmostEqual(actual, 3150.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertNotEqual(card["status"], "empty")
        self.assertRawIn(3150.0, card)
        self.assertRawIn(3000.0, card)

    def test_over_threshold_alert_records_invariant(self):
        # UX-002: "{n} budget lines over threshold" = is_alert rows of the latest version in the period, same domain as the link
        self._approve()
        alert = self._item(self._data(), "alerts", ALERT)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 1)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], BVA_ACTION)
        self._assert_records_invariant(alert)

    def test_over_threshold_alert_only_in_period(self):
        # AD-003: January has no flagged row, February has one
        self._approve()
        self.assertIsNone(self._item(self._data(kind="month", ref="2026-01-15"), "alerts", ALERT))
        alert = self._item(self._data(kind="month", ref="2026-02-15"), "alerts", ALERT)
        self.assertEqual(alert["count"], 1)
        self._assert_records_invariant(alert)

    def test_chart_one_label_per_month_for_year(self):
        # UX-002 chart 2: budget vs actual per month with data, plan and actual series
        self._approve()
        chart = self._item(self._data(), "charts", CHART)
        self.assertTrue(chart)
        self.assertEqual(chart["type"], "bar")
        self.assertEqual(len(chart["labels"]), 3)
        totals = sorted(round(sum(dataset["data"]), 2) for dataset in chart["datasets"])
        self.assertEqual(totals, [3000.0, 3150.0])

    def test_chart_absent_for_month(self):
        # UX-002: chart hidden for the "month" period
        self._approve()
        self.assertIsNone(self._item(self._data(kind="month", ref="2026-02-15"), "charts", CHART))

    def test_chart_grouped_by_year_for_all(self):
        # UX-002: "all" groups the chart by year
        self._approve()
        chart = self._item(self._data(kind="all"), "charts", CHART)
        self.assertTrue(chart)
        self.assertEqual(len(chart["labels"]), 1)
        self.assertIn("2026", chart["labels"][0])

    def test_budget_keys_absent_without_read_on_bva(self):
        # AD-005 / SC-082: no read on the BvA view -> KPI, card, alert and chart omitted, no error
        self._approve()
        data = self._data(user=self.user)
        self.assertTrue(self._item(data, "kpis", KPI), "positive control: the user sees the KPI")
        self._revoke_read("cmc.budget.bva")
        data = self._data(user=self.user)
        self.assertFalse(self._keys(data, "kpis", "budget."))
        self.assertFalse(self._keys(data, "cards", "budget."))
        self.assertFalse(self._keys(data, "alerts", "budget."))
        self.assertFalse(self._keys(data, "charts", "budget."))

    def test_other_company_budget_is_ignored(self):
        # AD-004: an approved budget of another company never shows
        other = self.env["res.company"].create({"name": "DASH budget other"})
        other_version = self._budget(other)[0]
        self._done(other_version)
        self.env.flush_all()
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))
