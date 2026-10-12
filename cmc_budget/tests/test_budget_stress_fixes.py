"""Stress test 2026-10 (corsia B) - regression tests for FX-009 (DL-004/006), FX-016, DL-008, FX-017 on cmc_budget.

CONTRACT (new or changed, binding for the developer)
====================================================
FX-009 / DL-004 / DL-006 - ``cmc.budget.bva``:
  - only APPROVED versions (``state = 'done'``) have rows: a draft version is never in the view;
  - the actual is joined once per approved version, so grouping by version shows it once per version and never
    multiplied on a single row;
  - plan and actual of the same version x month x center x cost element are ONE row: behavior and business line
    of the plan line do not split the row away from the actual (actuals carry neither);
  - presentation sign: cost ``plan_amount`` / ``actual_amount`` are POSITIVE figures (1,000, not -1,000), like the
    closing and the cost registrations; ``variance`` stays "positive = favourable" (cost: plan - actual);
  - the menu action ``cmc_budget.action_cmc_budget_bva`` opens with a ``search_default_*version*`` key in its
    context, so the default view compares against ONE version (never sums several versions).
  NOTE for the developer: ``test_budget_bva.py`` (natural sign, behaviour/business-line rows of RN-004) and the
  ``common.py`` contract §4 fix the old convention and must be updated together with this change.
FX-016 - annual budget with seasonality:
  - ``cmc.plan.version.seasonality_id`` Many2one ``cmc.budget.seasonality`` (optional default profile of the version);
  - wizard ``cmc.budget.annual.wizard`` (TransientModel): ``version_id`` (default = context ``active_id`` when
    ``active_model`` is ``cmc.plan.version``), ``kind`` (cost/revenue), ``center_id``, ``element_id``,
    ``behavior``, ``amount`` (positive annual figure), ``seasonality_id`` (default = the version's
    ``seasonality_id``); ``action_apply()`` calls ``version._cmc_budget_add(vals, amount, seasonality)``;
  - an ``ir.actions.act_window`` on ``cmc.budget.annual.wizard`` is reachable from a button of the plan version form.
DL-008 - ``cmc.budget.line.date`` label contains "Month" (it_IT: "Mese").
RN-003 - ``cmc.budget.bva.is_latest_version`` ("Latest Approved Version", default filter of the menu) marks, per
  company, the approved budget/forecast version whose span CONTAINS the current month (SQL ``CURRENT_DATE``); only
  when no approved version covers today it falls back to the one with the latest ``date_to``. A next-year budget
  approved in advance must not take the default view away from the running year.
FX-017 - it_IT labels: the callable selection ``cmc.budget.bva.version_type`` returns translated labels; the
  ``state`` statusbar of the cmc_budget runs reads "Calcolato" in it_IT.
"""

import re
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from .common import CmcBudgetCase, CmcBudgetCommon, d


@tagged("post_install", "-at_install")
class TestBudgetBvaStress(CmcBudgetCase):
    """FX-009 / DL-004 / DL-006: the actual is one fact, read against approved versions, presented positive."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bva = cls.env["cmc.budget.bva"]
        cls.center = cls._center("BUD fx009 Montaggio Z")
        cls.business_line = cls.AA.create({"name": "BUD fx009 BL", "plan_id": cls.plan.id, "company_id": cls.company.id})
        cls.approved_a = cls._version(name="BUD fx009 Budget A")
        cls._line(cls.approved_a, cls.center, 3, 5000.0, business_line_id=cls.business_line.id)
        cls.approved_b = cls._version(name="BUD fx009 Budget B")
        cls._line(cls.approved_b, cls.center, 1, 100.0)
        cls.draft = cls._version(name="BUD fx009 Draft")
        cls._line(cls.draft, cls.center, 3, 3000.0)
        # SC-032: "comportamento qualsiasi" - the actual behavior differs from the plan line (fixed)
        cls._cost(cls.center, 1000.0, d(3, 10), behavior="variable")
        for version in cls.approved_a | cls.approved_b:
            version.with_user(cls.manager).action_approve()
        cls.env.flush_all()

    def _march(self, version=None):
        domain = [("center_id", "=", self.center.id), ("date", "=", d(3)), ("kind", "=", "cost")]
        if version:
            domain.append(("version_id", "=", version.id))
        return domain

    def test_fx009_actual_once_per_approved_version_draft_absent(self):
        # FX-009 / DL-004: grouped by version the 1,000 actual shows once per APPROVED version; the draft is not compared
        groups = dict(self.Bva._read_group(self._march(), ["version_id"], ["actual_amount:sum"]))
        self.assertEqual(set(groups), {self.approved_a, self.approved_b}, groups)
        self.assertAlmostEqual(groups[self.approved_a], 1000.0)
        self.assertAlmostEqual(groups[self.approved_b], 1000.0)

    def test_fx009_draft_version_has_no_row(self):
        # FX-009 / DL-004: a draft budget is work in progress, never a reference of the BvA
        self.assertFalse(self.Bva.search_count([("version_id", "=", self.draft.id)]))

    def test_fx009_plan_and_actual_on_same_row(self):
        # FX-009 / DL-004: same center x month x element -> one row, whatever behavior / business line the plan carries
        rows = self.Bva.search(self._march(self.approved_a))
        self.assertEqual(len(rows), 1, rows.read(["element_id", "behavior", "business_line_id", "plan_amount", "actual_amount"]))
        self.assertRecordValues(rows, [{"element_id": self.rent.id, "plan_amount": 5000.0, "actual_amount": 1000.0}])

    def test_fx009_costs_positive_and_saving_favourable(self):
        # DL-006: costs presented positive like closing and registrations; spending 1,000 of 5,000 is +4,000 favourable
        rows = self.Bva.search(self._march(self.approved_a))
        self.assertRecordValues(rows, [{"plan_amount": 5000.0, "actual_amount": 1000.0, "variance": 4000.0}])

    def test_fx009_unbudgeted_cost_unfavourable(self):
        # DL-006: an actual cost without plan stays an unfavourable (negative) variance, its amount positive
        rows = self.Bva.search(self._march(self.approved_b))
        self.assertEqual(len(rows), 1, rows.read(["element_id", "behavior", "plan_amount", "actual_amount"]))
        self.assertRecordValues(rows, [{"plan_amount": 0.0, "actual_amount": 1000.0, "variance": -1000.0}])

    def test_fx009_default_action_filters_one_version(self):
        # DL-004: the report opens on ONE version, never summing the versions
        action = self.env.ref("cmc_budget.action_cmc_budget_bva")
        if action._name == "ir.actions.server":
            context = str((action.run() or {}).get("context") or "")
        else:
            context = action.context or ""
        self.assertRegex(context, re.compile(r"search_default_\w*version"), "the BvA action has no default version filter")


@tagged("post_install", "-at_install")
class TestBudgetAnnualWizard(CmcBudgetCase):
    """FX-016: a seasonality profile is usable from the UI when entering an annual budget."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.center = cls._center("BUD fx016 center")
        cls.version = cls._version(name="BUD fx016 2033")
        cls.profile = cls.env["cmc.budget.seasonality"].create({
            "name": "BUD fx016 December peak", "company_id": cls.company.id,
            "line_ids": [(0, 0, {"month": month, "weight": 2.0 if month == 12 else 1.0}) for month in range(1, 13)],
        })

    @property
    def Wizard(self):  # noqa: N802 - resolved per test: the model does not exist until FX-016 is implemented
        return self.env["cmc.budget.annual.wizard"]

    def _apply(self, user=None, **vals):
        Wizard = self.Wizard.with_user(user or self.manager).with_context(active_model="cmc.plan.version", active_id=self.version.id)
        wizard = Wizard.create({"version_id": self.version.id, **vals})
        wizard.action_apply()
        return self.version.budget_line_ids

    def _by_month(self, lines):
        return {line.date.month: line.figure for line in lines}

    def test_fx016_annual_revenue_follows_seasonality(self):
        # FX-016 AC: weight 2 in December, 1 elsewhere, revenue 13,000 -> December 2,000, every other month 1,000
        lines = self._apply(kind="revenue", behavior="variable", amount=13000.0, seasonality_id=self.profile.id)
        self.assertEqual(self._by_month(lines.filtered(lambda line: line.kind == "revenue")), {m: 2000.0 if m == 12 else 1000.0 for m in range(1, 13)})

    def test_fx016_variable_cost_follows_seasonality(self):
        # FX-016: variable cost is phased by the profile too
        lines = self._apply(kind="cost", center_id=self.center.id, element_id=self.energy.id, behavior="variable",
                            amount=13000.0, seasonality_id=self.profile.id)
        self.assertEqual(self._by_month(lines), {m: 2000.0 if m == 12 else 1000.0 for m in range(1, 13)})

    def test_fx016_fixed_cost_ignores_seasonality(self):
        # BUD-02: a fixed cost stays annual / 12 even with a profile chosen
        lines = self._apply(kind="cost", center_id=self.center.id, element_id=self.rent.id, behavior="fixed",
                            amount=12000.0, seasonality_id=self.profile.id)
        self.assertEqual(self._by_month(lines), dict.fromkeys(range(1, 13), 1000.0))

    def test_fx016_wizard_defaults_from_version(self):
        # FX-016: the version's default profile and the version itself prefill the wizard opened from it
        self.version.seasonality_id = self.profile
        wizard = self.Wizard.with_user(self.manager).with_context(active_model="cmc.plan.version", active_id=self.version.id).create({})
        self.assertEqual(wizard.version_id, self.version)
        self.assertEqual(wizard.seasonality_id, self.profile)

    def test_fx016_version_has_default_seasonality_field(self):
        # FX-016: the plan version carries an optional default seasonality profile
        field = self.env["cmc.plan.version"].fields_get(["seasonality_id"]).get("seasonality_id")
        self.assertTrue(field, "cmc.plan.version has no seasonality_id")
        self.assertEqual((field["type"], field["relation"], field.get("required", False)), ("many2one", "cmc.budget.seasonality", False))

    def test_fx016_wizard_reachable_from_version_form(self):
        # FX-016: the user reaches the wizard from the plan version form (no menu hunting)
        actions = self.env["ir.actions.act_window"].search([("res_model", "=", "cmc.budget.annual.wizard")])
        self.assertTrue(actions, "no window action opens cmc.budget.annual.wizard")
        arch = self.env["cmc.plan.version"].get_view(view_type="form")["arch"]
        self.assertTrue(any(f'name="{action.id}"' in arch for action in actions), "no button of the plan version form opens the wizard")


@tagged("post_install", "-at_install")
class TestBudgetLabelsStress(CmcBudgetCase):
    """DL-008 / FX-017: labels that tell the truth, in Italian too."""

    def _label(self, model, field, lang):
        return self.env[model].with_context(lang=lang).fields_get([field])[field]["string"]

    def _selection(self, model, field, lang):
        return dict(self.env[model].with_context(lang=lang).fields_get([field])[field]["selection"])

    def test_dl008_budget_line_date_labelled_month(self):
        # DL-008: the date of a budget line is normalised to the 1st: the label says it is a month
        self.assertIn("Month", self._label("cmc.budget.line", "date", "en_US"))
        self.assertIn("Mese", self._label("cmc.budget.line", "date", "it_IT"))

    def test_fx017_bva_version_type_translated(self):
        # FX-017: the callable selection copies the plan version selection WITH its translated labels
        version_it = self._selection("cmc.plan.version", "version_type", "it_IT")
        self.assertNotEqual(version_it, self._selection("cmc.plan.version", "version_type", "en_US"), "fixture: cmc_base has no it_IT labels")
        self.assertEqual(self._selection("cmc.budget.bva", "version_type", "it_IT"), version_it)

    def test_fx017_run_statusbar_italian(self):
        # FX-017: "Bozza / Calcolato" on every cmc_budget run, as on ABC and closing
        for model in ("cmc.budget.variance.run", "cmc.budget.cash.run"):
            with self.subTest(model=model):
                self.assertEqual(self._selection(model, "state", "it_IT"), {"draft": "Bozza", "done": "Calcolato"})


@tagged("post_install", "-at_install")
class TestBudgetLatestVersionStress(CmcBudgetCommon, TransactionCase):
    """RN-003: the default BvA version is the one running today, not the one ending last."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # why: an own company, so approved versions of other tests/data of env.company cannot cover today
        cls._cmc_budget_setup(cls.env["res.company"].create({"name": "BUD RN003 Co"}))
        cls.Bva = cls.env["cmc.budget.bva"]
        cls.center = cls._center("BUD rn003 center")
        cls.year = fields.Date.context_today(cls.Version).year

    @classmethod
    def _approved(cls, name, year):
        version = cls._version(name=name, date_from=date(year, 1, 1), date_to=date(year, 12, 31))
        cls._line(version, cls.center, 6, 1000.0, date=date(year, 6, 1))
        version.with_user(cls.manager).action_approve()
        return version

    def _latest_flags(self, versions):
        self.env.flush_all()
        rows = self.Bva.search([("version_id", "in", versions.ids)])
        self.assertEqual(rows.version_id, versions, "fixture: every approved version has rows")
        return {version: set(rows.filtered(lambda r, v=version: r.version_id == v).mapped("is_latest_version")) for version in versions}

    def test_rn003_current_year_version_is_latest(self):
        # RN-003: with this year's and next year's budgets approved, the default view is this year's
        current = self._approved("BUD rn003 current", self.year)
        following = self._approved("BUD rn003 next", self.year + 1)
        flags = self._latest_flags(current | following)
        self.assertEqual(flags[current], {True})
        self.assertEqual(flags[following], {False})

    def test_rn003_fallback_latest_date_to_when_none_covers_today(self):
        # RN-003: no approved version covers today -> the one ending last is the default
        older = self._approved("BUD rn003 older", self.year - 3)
        newer = self._approved("BUD rn003 newer", self.year - 2)
        flags = self._latest_flags(older | newer)
        self.assertEqual(flags[newer], {True})
        self.assertEqual(flags[older], {False})


@tagged("post_install", "-at_install")
class TestBudgetLatestVersionBudgetFirst(CmcBudgetCommon, TransactionCase):
    """RN-006 (ronin): when a budget and a forecast both cover today, the default BvA view is the BUDGET.

    CONTRACT: among the approved versions of a company whose span contains the current month, an approved
    ``budget`` wins over an approved ``forecast`` regardless of the id / creation order; a forecast is the latest
    version only when no approved budget covers today. The RN-003 fallback (no version covers today -> latest
    ``date_to``) is unchanged.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # why: an own company, so approved versions of other tests/data of env.company cannot cover today
        cls._cmc_budget_setup(cls.env["res.company"].create({"name": "BUD RN006 Co"}))
        cls.Bva = cls.env["cmc.budget.bva"]
        cls.center = cls._center("BUD rn006 center")
        cls.year = fields.Date.context_today(cls.Version).year

    @classmethod
    def _approved(cls, name, year, version_type="budget"):
        version = cls._version(name=name, date_from=date(year, 1, 1), date_to=date(year, 12, 31), version_type=version_type)
        cls._line(version, cls.center, 6, 1000.0, date=date(year, 6, 1))
        version.with_user(cls.manager).action_approve()
        return version

    def _latest_flags(self, versions):
        self.env.flush_all()
        rows = self.Bva.search([("version_id", "in", versions.ids)])
        self.assertEqual(rows.version_id, versions, "fixture: every approved version has rows")
        return {version: set(rows.filtered(lambda r, v=version: r.version_id == v).mapped("is_latest_version")) for version in versions}

    def test_rn006_budget_wins_over_later_forecast(self):
        # RN-006: the forecast approved later (higher id) must not take the default view away from the budget
        budget = self._approved("BUD rn006 budget", self.year)
        forecast = self._approved("BUD rn006 forecast", self.year, version_type="forecast")
        self.assertGreater(forecast.id, budget.id, "fixture: the forecast is the newer version")
        flags = self._latest_flags(budget | forecast)
        self.assertEqual(flags[budget], {True})
        self.assertEqual(flags[forecast], {False})

    def test_rn006_forecast_latest_when_no_budget_covers_today(self):
        # RN-006: only a forecast covers today (the budget is last year's) -> the forecast is the default
        budget = self._approved("BUD rn006 old budget", self.year - 1)
        forecast = self._approved("BUD rn006 only forecast", self.year, version_type="forecast")
        flags = self._latest_flags(budget | forecast)
        self.assertEqual(flags[forecast], {True})
        self.assertEqual(flags[budget], {False})
