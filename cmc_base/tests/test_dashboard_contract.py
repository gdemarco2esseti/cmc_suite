"""Controller dashboard - cmc_base contract of ``cmc.cockpit.get_dashboard_data`` (UX-002, DASH-ADR AD-001..006).

Every test isolates itself in a NEW company (the dashboard reads ``self.env.company`` only) and runs the
dashboard AS a non-superuser ``group_cmc_user`` (no sudo semantics: a superuser would bypass the ACL checks
the contract relies on). Fixed reference dates (2026-06-15) except the default-period test.

The suite may be fully installed in the test database: contributor modules then add their own (empty-state)
cards, so these tests assert on the ``base.*`` keys and on structural invariants, never on the total count.
"""

from datetime import date

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time as _freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.service.model import call_kw
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import date_utils
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models import cmc_cockpit as cockpit_module
from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

REF = "2026-06-15"
YEAR = {"kind": "year", "ref": REF}
ALL = {"kind": "all"}
EXPECTED_AREAS = ["recording", "costing", "planning", "decision", "performance", "financial"]
EXPECTED_SLOTS = ("cost", "budget", "result", "capacity", "scorecard")
EXPECTED_KINDS = ("month", "quarter", "year", "all")
# KPI key -> slot, from DASH-devlead.md section 2 (every KPI key of the contract)
KPI_KEY_SLOT = {
    "base.cost": "cost",
    "base.capacity": "capacity",
    "abc.capacity": "capacity",
    "tdabc.capacity": "capacity",
    "budget.variance": "budget",
    "direct_costing.result": "result",
    "profitability.result": "result",
    "kpi.scorecard": "scorecard",
}
SEVERITY_RANK = {"danger": 0, "warning": 1, "info": 2}
CARD_STATUSES = {"up_to_date", "outdated", "attention", "empty"}
PAYLOAD_KEYS = {"header", "kpis", "alerts", "cards", "charts", "areas"}
PRIVATE_HOOKS = (
    "_cmc_dashboard_kpis",
    "_cmc_dashboard_alerts",
    "_cmc_dashboard_cards",
    "_cmc_dashboard_charts",
    "_cmc_dashboard_period",
    "_cmc_dashboard_latest_run",
)


def by_key(items, key):
    return next((item for item in items if item["key"] == key), None)


def keys(items, prefix=""):
    return {item["key"] for item in items if item["key"].startswith(prefix)}


class DashboardCase(TransactionCase):
    """Shared fixtures: an isolated EUR company (+ a second one for isolation), a cost center per company,
    cost elements, and a controlling user acting on the dashboard."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.active = True
        cls.company = cls.env["res.company"].create({"name": "CMC Dash A", "currency_id": cls.eur.id})
        cls.company_b = cls.env["res.company"].create({"name": "CMC Dash B", "currency_id": cls.eur.id})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.cmc_user = new_test_user(
            cls.env,
            login="cmc_dash_contract_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.company_b).ids)],
        )
        cls.center = cls._center("Dash center A", cls.company)
        cls.center_b = cls._center("Dash center B", cls.company_b)
        cls.el_mat = cls._element("Dash materials", "DSH_MAT", cls.company)
        cls.el_lab = cls._element("Dash labour", "DSH_LAB", cls.company)
        cls.el_b = cls._element("Dash materials B", "DSH_MATB", cls.company_b)

    # ------------------------------------------------------------------------------------------------ fixtures

    @classmethod
    def _center(cls, name, company):
        return cls.env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": company.id}
        )

    @classmethod
    def _element(cls, name, code, company):
        return cls.env["cmc.cost.element"].create({"name": name, "code": code, "company_id": company.id})

    def _line(self, cost, day, behavior="fixed", element=None, center=None, **extra):
        """A cost entry: `cost` is the positive magnitude, stored as a negative analytic amount (AD-005)."""
        center = center or self.center
        vals = {
            "name": "dash cost",
            "account_id": center.id,
            "amount": -cost,
            "date": day,
            "company_id": center.company_id.id,
            "cmc_behavior": behavior,
            "cmc_cost_element_id": element.id if element else False,
        }
        vals.update(extra)
        return self.AL.create(vals)

    def _classified(self, cost, day, element=None, center=None):
        return self._line(cost, day, element=element or self.el_mat, center=center)

    def _closing(self, date_from, date_to, done=True, company=None, residual=(), distributed=0.0):
        """A closing run of the real model; results are written as the engine does (sentinel context),
        `done` forced the same way (computing a closing here would need a whole center network)."""
        company = company or self.company
        run = self.env["cmc.closing.run"].create(
            {"date_from": date_from, "date_to": date_to, "method": "direct", "company_id": company.id}
        )
        engine = run.with_context(cmc_run_state=CMC_RUN_STATE)
        center = self.center if company == self.company else self.center_b
        for idx, amount in enumerate(residual or (0.0,)):
            target = center if idx == 0 else self._center(f"Dash residual {run.id}-{idx}", company)
            self.env["cmc.closing.line"].with_context(cmc_run_state=CMC_RUN_STATE).create(
                {
                    "run_id": run.id,
                    "center_id": target.id,
                    "cost_distributed": distributed if idx == 0 else 0.0,
                    "cost_residual": amount,
                }
            )
        if done:
            engine.write({"state": "done"})
        return run

    def _capacity(self, date_from, date_to, practical=100.0, actual=80.0, center=None):
        return self.env["cmc.capacity.analysis"].create(
            {
                "center_id": (center or self.center).id,
                "company_id": (center or self.center).company_id.id,
                "date_from": date_from,
                "date_to": date_to,
                "capacity_practical": practical,
                "capacity_actual": actual,
            }
        )

    def _full_base_fixture(self):
        """Everything cmc_base can contribute in year 2026: classified + unclassified cost, a done closing
        with residual (closing of June: the previous month vs today is NOT closed), a capacity analysis."""
        self._classified(1000.0, date(2026, 6, 10), element=self.el_mat)
        self._classified(200.0, date(2026, 6, 11), element=self.el_lab)
        self._line(50.0, date(2026, 6, 12), behavior="fixed", element=None)
        self._closing(date(2026, 6, 1), date(2026, 6, 30), residual=(25.0,), distributed=500.0)
        self._capacity(date(2026, 6, 1), date(2026, 6, 30))

    # ------------------------------------------------------------------------------------------------ calls

    def _cockpit(self, user=None):
        return self.env["cmc.cockpit"].with_user(user or self.cmc_user).with_context(allowed_company_ids=[self.company.id])

    def _dash(self, period=YEAR, user=None):
        return self._cockpit(user).get_dashboard_data(period)

    def _period(self, period=YEAR):
        return self._cockpit()._cmc_dashboard_period(period)


# ======================================================================================================== payload


@tagged("post_install", "-at_install")
class TestDashboardPayload(DashboardCase):
    """Shape of the payload and the central assembly rules (areas, ordering, unique keys, KPI slots)."""

    def test_module_constants(self):
        # DASH-devlead section 1: the enum of areas, the KPI slots and the period kinds are module constants
        areas = getattr(cockpit_module, "DASHBOARD_AREAS", None)
        self.assertTrue(areas, "cmc_base.models.cmc_cockpit.DASHBOARD_AREAS missing")
        self.assertEqual([a[0] for a in areas], EXPECTED_AREAS)
        self.assertEqual([a[1] for a in areas], sorted(a[1] for a in areas), "area sequences must follow tab order")
        self.assertEqual(tuple(getattr(cockpit_module, "KPI_SLOTS", ())), EXPECTED_SLOTS)
        self.assertEqual(tuple(getattr(cockpit_module, "PERIOD_KINDS", ())), EXPECTED_KINDS)

    def test_payload_top_level_keys(self):
        # AD-002: the payload is exactly {header, kpis, alerts, cards, charts, areas}; the header carries no company
        data = self._dash()
        self.assertEqual(set(data), PAYLOAD_KEYS)
        header = data["header"]
        self.assertEqual(set(header), {"period", "currency", "global_empty"})
        self.assertNotIn("company", header)
        self.assertEqual(
            set(header["period"]), {"kind", "date_from", "date_to", "label", "prev_ref", "next_ref"}
        )
        self.assertEqual(set(header["currency"]), {"symbol", "position"})
        self.assertEqual(header["currency"]["symbol"], self.eur.symbol)
        for name in ("kpis", "alerts", "cards", "charts", "areas"):
            self.assertIsInstance(data[name], list, name)

    def test_payload_base_contributions(self):
        # UX-002 visibility "only cmc_base": KPI 1 + 4, alerts unclassified/residual/closing-not-run, cards
        # Chiusura + Capacita, chart 1 - the base.* part of the payload is exactly that
        self._full_base_fixture()
        data = self._dash()
        self.assertEqual(keys(data["cards"], "base."), {"base.closing", "base.capacity"})
        self.assertEqual(keys(data["kpis"], "base."), {"base.cost", "base.capacity"})
        self.assertEqual(keys(data["alerts"], "base."), {"base.unclassified", "base.residual", "base.closing_missing"})
        self.assertEqual(keys(data["charts"], "base."), {"base.cost_by_element"})
        closing = by_key(data["cards"], "base.closing")
        capacity = by_key(data["cards"], "base.capacity")
        self.assertEqual(closing["area"], "recording")
        self.assertEqual(capacity["area"], "decision")
        self.assertEqual(closing["action"], "cmc_base.action_cmc_closing_run")
        self.assertEqual(capacity["action"], "cmc_base.action_cmc_capacity")

    def test_card_shape(self):
        # AD-002 / R2: every card has the full key set, a known status, an action xmlid or None, a list domain
        self._full_base_fixture()
        card_keys = {
            "key", "area", "sequence", "title", "subtitle", "status", "run_date_from", "run_date_to",
            "in_period", "figures", "action", "domain",
        }
        for card in self._dash()["cards"]:
            with self.subTest(card=card["key"]):
                self.assertTrue(card_keys <= set(card), f"missing {card_keys - set(card)}")
                self.assertIn(card["status"], CARD_STATUSES)
                self.assertTrue(card["action"] is None or isinstance(card["action"], str))
                self.assertIsInstance(card["domain"], list)
                for figure in card["figures"]:
                    self.assertTrue({"key", "label", "value", "raw", "kind", "status"} <= set(figure))

    def test_chart_shape(self):
        # R3: chart payload {key,type,title,labels,datasets[{label,data,formatted}],horizontal}
        self._full_base_fixture()
        chart = by_key(self._dash()["charts"], "base.cost_by_element")
        self.assertTrue(chart)
        self.assertTrue({"key", "type", "title", "labels", "datasets", "horizontal"} <= set(chart))
        self.assertEqual(chart["type"], "bar")
        self.assertIs(chart["horizontal"], True)
        for dataset in chart["datasets"]:
            self.assertEqual(len(dataset["data"]), len(chart["labels"]))
            self.assertEqual(len(dataset["formatted"]), len(chart["labels"]))
            self.assertTrue(all(isinstance(v, str) and v for v in dataset["formatted"]))

    def test_card_areas_in_enum_and_keys_unique(self):
        # AD-001: unknown area = error; keys are module-prefixed and unique inside each list
        # (a KPI and a card may share a key, e.g. base.capacity: uniqueness is per list)
        self._full_base_fixture()
        data = self._dash()
        for card in data["cards"]:
            self.assertIn(card["area"], EXPECTED_AREAS, card["key"])
        for name in ("kpis", "alerts", "cards", "charts"):
            with self.subTest(section=name):
                item_keys = [item["key"] for item in data[name]]
                self.assertEqual(len(item_keys), len(set(item_keys)), f"duplicate keys in {name}: {item_keys}")
                for key in item_keys:
                    self.assertRegex(key, r"^[a-z_]+\.[a-z_]+$", "keys are '<module>.<name>'")

    def test_areas_ordered_and_only_non_empty(self):
        # UX-002: areas in tab order, an area appears only when it has at least one card
        self._full_base_fixture()
        data = self._dash()
        card_areas = {card["area"] for card in data["cards"]}
        self.assertEqual([a["key"] for a in data["areas"]], [a for a in EXPECTED_AREAS if a in card_areas])
        for area in data["areas"]:
            self.assertTrue(isinstance(area["label"], str) and area["label"], area["key"])

    def test_cards_sorted_by_area_sequence_key(self):
        # central sort: (area sequence, card sequence, key)
        self._full_base_fixture()
        cards = self._dash()["cards"]
        order = [(EXPECTED_AREAS.index(c["area"]), c["sequence"], c["key"]) for c in cards]
        self.assertEqual(order, sorted(order))

    def test_kpis_at_most_five_sorted_by_slot(self):
        # KPI row: at most 5 tiles, one per slot, in KPI_SLOTS order
        self._full_base_fixture()
        kpis = self._dash()["kpis"]
        self.assertLessEqual(len(kpis), 5)
        slots = []
        for kpi in kpis:
            self.assertIn(kpi["key"], KPI_KEY_SLOT, f"KPI key outside the contract: {kpi['key']}")
            slots.append(KPI_KEY_SLOT[kpi["key"]])
        self.assertEqual(len(slots), len(set(slots)), f"two KPIs in one slot: {slots}")
        self.assertEqual(slots, sorted(slots, key=EXPECTED_SLOTS.index))

    def test_alerts_sorted_by_severity_then_magnitude(self):
        # "Da verificare": danger > warning > info
        self._full_base_fixture()
        alerts = self._dash()["alerts"]
        ranks = [SEVERITY_RANK[a["severity"]] for a in alerts]
        self.assertEqual(ranks, sorted(ranks))
        for alert in alerts:
            self.assertTrue({"key", "severity", "label", "count", "action", "domain"} <= set(alert), alert["key"])


# ========================================================================================================= period


@tagged("post_install", "-at_install")
class TestDashboardPeriod(DashboardCase):
    """AD-003: calendar periods, untrusted input whitelisted, default = current year."""

    def _header_period(self, period):
        return self._dash(period)["header"]["period"]

    def test_period_month(self):
        p = self._header_period({"kind": "month", "ref": REF})
        self.assertEqual((p["kind"], p["date_from"], p["date_to"]), ("month", "2026-06-01", "2026-06-30"))
        self.assertTrue(isinstance(p["label"], str) and p["label"])

    def test_period_quarter(self):
        p = self._header_period({"kind": "quarter", "ref": REF})
        self.assertEqual((p["kind"], p["date_from"], p["date_to"]), ("quarter", "2026-04-01", "2026-06-30"))

    def test_period_year(self):
        p = self._header_period(YEAR)
        self.assertEqual((p["kind"], p["date_from"], p["date_to"]), ("year", "2026-01-01", "2026-12-31"))

    def test_period_all_has_no_dates(self):
        p = self._header_period(ALL)
        self.assertEqual(p["kind"], "all")
        self.assertIsNone(p["date_from"])
        self.assertIsNone(p["date_to"])

    def test_period_prev_next_ref_month(self):
        # the arrows re-request the dashboard with prev_ref / next_ref: they land on the adjacent month
        p = self._header_period({"kind": "month", "ref": REF})
        prev = self._header_period({"kind": "month", "ref": p["prev_ref"]})
        nxt = self._header_period({"kind": "month", "ref": p["next_ref"]})
        self.assertEqual(prev["date_from"], "2026-05-01")
        self.assertEqual(nxt["date_from"], "2026-07-01")

    def test_period_prev_next_ref_year(self):
        p = self._header_period(YEAR)
        self.assertEqual(self._header_period({"kind": "year", "ref": p["prev_ref"]})["date_from"], "2025-01-01")
        self.assertEqual(self._header_period({"kind": "year", "ref": p["next_ref"]})["date_from"], "2027-01-01")

    def test_period_default_is_current_year(self):
        # no period from the client = current calendar year (context_today)
        today = fields.Date.context_today(self._cockpit())
        expected = ("year", date(today.year, 1, 1).isoformat(), date(today.year, 12, 31).isoformat())
        for p in (self._dash(None)["header"]["period"], self._cockpit().get_dashboard_data()["header"]["period"]):
            self.assertEqual((p["kind"], p["date_from"], p["date_to"]), expected)

    def test_period_invalid_kind_raises(self):
        with self.assertRaises(UserError):
            self._dash({"kind": "week", "ref": REF})

    def test_period_invalid_ref_raises(self):
        with self.assertRaises(UserError):
            self._dash({"kind": "month", "ref": "2026-13-45"})

    def test_internal_period_previous_is_same_period_last_year(self):
        # internal period dict: date objects, prev = same period of the previous year (for the cost trend)
        p = self._period({"kind": "quarter", "ref": REF})
        self.assertEqual(p["kind"], "quarter")
        self.assertEqual((p["date_from"], p["date_to"]), (date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual((p["prev_date_from"], p["prev_date_to"]), (date(2025, 4, 1), date(2025, 6, 30)))
        p_all = self._period(ALL)
        self.assertIsNone(p_all["date_from"])
        self.assertIsNone(p_all["prev_date_from"])


# ==================================================================================================== cost KPI


@tagged("post_install", "-at_install")
class TestDashboardCostKpi(DashboardCase):
    """KPI 1 "Costi rilevati": classified cost of the period, delta vs the same period last year."""

    def test_cost_kpi_raw_sums_classified_cost_in_period(self):
        self._classified(100.0, date(2026, 3, 1), element=self.el_mat)
        self._classified(200.0, date(2026, 9, 1), element=self.el_lab)
        self._classified(50.0, date(2025, 3, 1))  # previous year: not in the 2026 figure
        kpi = by_key(self._dash()["kpis"], "base.cost")
        self.assertTrue(kpi)
        self.assertAlmostEqual(kpi["raw"], 300.0)
        self.assertTrue(isinstance(kpi["value"], str) and kpi["value"] and kpi["value"] != "—")

    def test_cost_kpi_month_period(self):
        self._classified(100.0, date(2026, 6, 1))
        self._classified(200.0, date(2026, 7, 1))
        kpi = by_key(self._dash({"kind": "month", "ref": REF})["kpis"], "base.cost")
        self.assertAlmostEqual(kpi["raw"], 100.0)

    def test_cost_kpi_trend_vs_previous_year(self):
        # delta % vs same period of the previous year: 300 vs 50 is up
        self._classified(300.0, date(2026, 3, 1))
        self._classified(50.0, date(2025, 3, 1))
        trend = by_key(self._dash()["kpis"], "base.cost")["trend"]
        self.assertTrue(trend, "a cost with a previous-year comparison has a trend")
        self.assertEqual(trend["direction"], "up")
        self.assertIsInstance(trend["favourable"], bool)
        self.assertTrue(isinstance(trend["text"], str) and trend["text"])

    def test_cost_kpi_no_trend_for_all(self):
        self._classified(300.0, date(2026, 3, 1))
        self._classified(50.0, date(2025, 3, 1))
        kpi = by_key(self._dash(ALL)["kpis"], "base.cost")
        self.assertAlmostEqual(kpi["raw"], 350.0)
        self.assertIsNone(kpi["trend"])

    def test_cost_kpi_dash_when_no_cost_in_period(self):
        # data exists but none in the period: tile shown with "—" (raw None), never a fake 0
        self._classified(50.0, date(2025, 3, 1))
        kpi = by_key(self._dash()["kpis"], "base.cost")
        self.assertTrue(kpi, "the company has costs: the tile stays")
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")

    def test_cost_kpi_omitted_when_never_any_cost(self):
        # never any data: the tile is omitted
        self.assertIsNone(by_key(self._dash()["kpis"], "base.cost"))

    def test_company_isolation(self):
        # another company's costs and closings are never counted in the current company's dashboard
        self._classified(100.0, date(2026, 3, 1))
        self._classified(999.0, date(2026, 3, 1), element=self.el_b, center=self.center_b)
        self._closing(date(2026, 12, 1), date(2026, 12, 31), company=self.company_b, residual=(77.0,))
        data = self._dash()
        self.assertAlmostEqual(by_key(data["kpis"], "base.cost")["raw"], 100.0)
        self.assertEqual(by_key(data["cards"], "base.closing")["status"], "empty")
        self.assertIsNone(by_key(data["alerts"], "base.residual"))
        chart = by_key(data["charts"], "base.cost_by_element")
        self.assertEqual(chart["labels"], [self.el_mat.display_name])


# ============================================================================================ capacity / fallback


@tagged("post_install", "-at_install")
class TestDashboardCapacity(DashboardCase):
    """KPI 4 (capacity slot, base source prio 30) and the Capacita card; SC-082 omission when unreadable."""

    def _capacity_fixture(self):
        # 1000 fixed cost, practical 100 / actual 80 -> unused capacity cost 200, utilisation 80%
        self._classified(1000.0, date(2026, 6, 10))
        return self._capacity(date(2026, 6, 1), date(2026, 6, 30))

    def test_capacity_kpi_and_card(self):
        analysis = self._capacity_fixture()
        self.assertAlmostEqual(analysis.unused_capacity_cost, 200.0)
        data = self._dash({"kind": "month", "ref": REF})
        kpi = by_key(data["kpis"], "base.capacity")
        self.assertTrue(kpi)
        self.assertAlmostEqual(kpi["raw"], 200.0)
        card = by_key(data["cards"], "base.capacity")
        self.assertTrue(card)
        self.assertEqual(card["area"], "decision")
        self.assertNotEqual(card["status"], "empty")

    def test_capacity_kpi_and_card_omitted_when_unreadable(self):
        # SC-082: a role without read on cmc.capacity.analysis gets no capacity KPI from base and no card
        # (not 0, not "—"); the slot falls back to the next readable source, if any
        self._capacity_fixture()
        self.env["ir.model.access"].search([("model_id.model", "=", "cmc.capacity.analysis")]).perm_read = False
        self.assertFalse(self.env["cmc.capacity.analysis"].with_user(self.cmc_user).has_access("read"))
        data = self._dash({"kind": "month", "ref": REF})
        self.assertIsNone(by_key(data["kpis"], "base.capacity"))
        self.assertIsNone(by_key(data["cards"], "base.capacity"))
        capacity_kpis = [k for k in data["kpis"] if KPI_KEY_SLOT.get(k["key"]) == "capacity"]
        self.assertFalse([k for k in capacity_kpis if k["key"] == "base.capacity"])
        # the other base contributions are unaffected
        self.assertTrue(by_key(data["kpis"], "base.cost"))
        self.assertTrue(by_key(data["cards"], "base.closing"))


# ========================================================================================================= cards


@tagged("post_install", "-at_install")
class TestDashboardCards(DashboardCase):
    """Cards: latest DONE run regardless of period, shared run-status helper, in_period, empty state."""

    def test_card_empty_without_run(self):
        # installed + readable but no run: card present, status empty, CTA = run-list action (R2)
        card = by_key(self._dash()["cards"], "base.closing")
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertTrue(isinstance(card["subtitle"], str) and card["subtitle"])
        self.assertEqual(card["action"], "cmc_base.action_cmc_closing_run")
        self.assertIsNone(card["run_date_to"])

    def test_latest_done_run_ignores_newer_draft(self):
        self._closing(date(2026, 3, 1), date(2026, 3, 31), done=True)
        self._closing(date(2026, 6, 1), date(2026, 6, 30), done=False)
        card = by_key(self._dash()["cards"], "base.closing")
        self.assertEqual(card["run_date_to"], "2026-03-31")
        self.assertEqual(card["run_date_from"], "2026-03-01")

    def test_card_outdated_when_run_ends_before_period_end(self):
        self._closing(date(2026, 3, 1), date(2026, 3, 31))
        card = by_key(self._dash()["cards"], "base.closing")
        self.assertEqual(card["status"], "outdated")
        self.assertTrue(card["in_period"])

    def test_card_up_to_date_when_run_covers_period_end(self):
        self._closing(date(2026, 12, 1), date(2026, 12, 31))
        card = by_key(self._dash()["cards"], "base.closing")
        self.assertEqual(card["status"], "up_to_date")
        self.assertTrue(card["in_period"])

    def test_card_up_to_date_for_kind_all(self):
        self._closing(date(2026, 3, 1), date(2026, 3, 31))
        card = by_key(self._dash(ALL)["cards"], "base.closing")
        self.assertEqual(card["status"], "up_to_date")
        self.assertTrue(card["in_period"])

    def test_card_shown_out_of_period(self):
        # safety net: a module with data always shows its card, even when its latest run is out of period
        self._closing(date(2025, 12, 1), date(2025, 12, 31))
        card = by_key(self._dash()["cards"], "base.closing")
        self.assertEqual(card["status"], "outdated")
        self.assertFalse(card["in_period"])

    def test_card_attention_when_alert_references_it(self):
        # a run covering the period end but with residual cost: the alert turns the card to "attention"
        self._closing(date(2026, 12, 1), date(2026, 12, 31), residual=(25.0,))
        data = self._dash()
        self.assertTrue(by_key(data["alerts"], "base.residual"))
        self.assertEqual(by_key(data["cards"], "base.closing")["status"], "attention")

    def test_run_status_helper(self):
        # ONE shared helper: covers period end -> up_to_date; undated artefacts and kind all -> up_to_date
        cockpit = self._cockpit()
        year = self._period(YEAR)
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2026, 12, 31), year), "up_to_date")
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2027, 1, 31), year), "up_to_date")
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2026, 3, 31), year), "outdated")
        self.assertEqual(cockpit._cmc_dashboard_run_status(None, year), "up_to_date")
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2020, 1, 31), self._period(ALL)), "up_to_date")

    def test_in_period_helper(self):
        cockpit = self._cockpit()
        year = self._period(YEAR)
        self.assertTrue(cockpit._cmc_dashboard_in_period(date(2026, 1, 1), date(2026, 1, 31), year))
        self.assertTrue(cockpit._cmc_dashboard_in_period(date(2025, 12, 1), date(2026, 1, 31), year))
        self.assertFalse(cockpit._cmc_dashboard_in_period(date(2025, 1, 1), date(2025, 12, 31), year))
        self.assertFalse(cockpit._cmc_dashboard_in_period(None, None, year))
        self.assertTrue(cockpit._cmc_dashboard_in_period(date(2020, 1, 1), date(2020, 1, 31), self._period(ALL)))

    def test_latest_run_helper_done_only_and_company(self):
        # F1: explicit order; state=done filter + company domain built in
        self._closing(date(2026, 3, 1), date(2026, 3, 31))
        self._closing(date(2026, 6, 1), date(2026, 6, 30), done=False)
        self._closing(date(2026, 9, 1), date(2026, 9, 30), company=self.company_b)
        Run = self._cockpit().env["cmc.closing.run"]
        latest = self._cockpit()._cmc_dashboard_latest_run(Run, ["date_from", "date_to"], "date_to desc, id desc")
        self.assertTrue(latest)
        self.assertEqual(fields.Date.to_date(latest["date_to"]), date(2026, 3, 31))
        self.assertIsNone(
            self._cockpit()._cmc_dashboard_latest_run(
                Run, ["date_to"], "date_to desc, id desc", domain=[("date_to", "<", "2026-01-01")]
            )
        )


@tagged("post_install", "-at_install")
class TestDashboardCurrentPeriodCap(DashboardCase):
    """RN-002: the period containing today is year-to-date, compared with the same span of last year."""

    freeze_time = _freeze_time("2026-10-13 12:00:00")

    def _p(self, kind, ref):
        return self._cockpit()._cmc_dashboard_period({"kind": kind, "ref": ref})

    def test_current_period_capped_at_today(self):
        year = self._p("year", "2026-10-13")
        self.assertEqual((year["date_from"], year["date_to"]), (date(2026, 1, 1), date(2026, 10, 13)))
        self.assertEqual((year["prev_date_from"], year["prev_date_to"]), (date(2025, 1, 1), date(2025, 10, 13)))
        month = self._p("month", "2026-10-01")
        self.assertEqual((month["date_to"], month["prev_date_to"]), (date(2026, 10, 13), date(2025, 10, 13)))
        header = self._dash({"kind": "year", "ref": "2026-10-13"})["header"]["period"]
        self.assertEqual((header["date_to"], header["label"]), ("2026-10-13", "2026"))

    def test_past_future_and_all_periods_unchanged(self):
        self.assertEqual(self._p("year", "2025-06-15")["date_to"], date(2025, 12, 31))
        self.assertEqual(self._p("year", "2027-06-15")["date_to"], date(2027, 12, 31))
        self.assertEqual(self._p("quarter", "2026-05-15")["date_to"], date(2026, 6, 30))
        self.assertIsNone(self._p("all", None)["date_to"])

    def test_leap_day_today_is_safe(self):
        with _freeze_time("2028-02-29 12:00:00"):
            period = self._p("year", "2028-02-29")
        self.assertEqual(period["prev_date_to"], date(2027, 2, 28))

    def test_cost_trend_is_like_for_like(self):
        # last year's cost after 13 Oct is outside the comparison span
        self._classified(100.0, date(2026, 3, 1))
        self._classified(100.0, date(2025, 3, 1))
        self._classified(900.0, date(2025, 11, 1))
        trend = by_key(self._dash({"kind": "year", "ref": "2026-10-13"})["kpis"], "base.cost")["trend"]
        self.assertEqual(trend["direction"], "flat")

    def test_run_status_relative_to_today(self):
        period = self._p("year", "2026-10-13")
        cockpit = self._cockpit()
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2026, 10, 13), period), "up_to_date")
        self.assertEqual(cockpit._cmc_dashboard_run_status(date(2026, 9, 30), period), "outdated")


# ======================================================================================================== alerts


@tagged("post_install", "-at_install")
class TestDashboardAlerts(DashboardCase):
    """"Da verificare": record-scope alerts count exactly what their link opens; run-scope alerts open the run."""

    def _unclassified_fixture(self):
        self._classified(100.0, date(2026, 6, 1))
        self._line(50.0, date(2026, 6, 2), behavior="fixed", element=None)  # behavior, no element
        self._line(30.0, date(2026, 6, 3), behavior=False, element=None)  # negative, unclassified
        self._line(-40.0, date(2026, 6, 4), behavior=False, element=None)  # positive unclassified = revenue: out
        self._line(70.0, date(2026, 6, 5), behavior=False, element=None, cmc_excluded=True)  # twin: out
        self._line(20.0, date(2025, 6, 5), behavior=False, element=None)  # previous year: out
        # other company: must not be counted (the alert domain carries the company filter, AD-002)
        self._line(60.0, date(2026, 6, 5), behavior=False, element=None, center=self.center_b)

    def test_unclassified_alert_count_and_amount(self):
        self._unclassified_fixture()
        alert = by_key(self._dash()["alerts"], "base.unclassified")
        self.assertTrue(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["count"], 2)
        self.assertAlmostEqual(alert["amount"], 80.0)
        self.assertEqual(alert["action"], "cmc_base.action_cmc_analytic_lines")

    def test_unclassified_alert_domain_invariant(self):
        # F3: the list opened by the alert (action domain AND alert domain) has exactly `count` records;
        # counted as superuser, so only the domain itself (incl. company filter) can make it match
        self._unclassified_fixture()
        alert = by_key(self._dash()["alerts"], "base.unclassified")
        action = self.env["ir.actions.actions"]._for_xml_id(alert["action"])
        action_domain = action.get("domain") or []
        if isinstance(action_domain, str):
            action_domain = safe_eval(action_domain, {"uid": self.cmc_user.id})
        domain = Domain(action_domain) & Domain(alert["domain"])
        self.assertEqual(self.env[action["res_model"]].search_count(domain), alert["count"])

    def test_unclassified_alert_absent_when_all_classified(self):
        self._classified(100.0, date(2026, 6, 1))
        self.assertIsNone(by_key(self._dash()["alerts"], "base.unclassified"))

    def test_residual_alert_scope_run(self):
        run = self._closing(date(2026, 12, 1), date(2026, 12, 31), residual=(10.0, 15.0))
        alert = by_key(self._dash()["alerts"], "base.residual")
        self.assertTrue(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertAlmostEqual(alert["amount"], 25.0)
        self.assertEqual(alert["action"], "cmc_base.action_cmc_closing_run")
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", run.id]])

    def test_residual_alert_only_on_latest_done_run(self):
        self._closing(date(2026, 3, 1), date(2026, 3, 31), residual=(10.0,))
        self._closing(date(2026, 6, 1), date(2026, 6, 30), residual=(0.0,))
        self._closing(date(2026, 9, 1), date(2026, 9, 30), done=False, residual=(99.0,))
        self.assertIsNone(by_key(self._dash()["alerts"], "base.residual"))

    def test_closing_missing_absent_without_any_closing(self):
        # a company that never closes does not get nagged
        self.assertIsNone(by_key(self._dash()["alerts"], "base.closing_missing"))

    def test_closing_missing_info_when_previous_month_not_closed(self):
        self._closing(date(2020, 1, 1), date(2020, 1, 31))
        alert = by_key(self._dash()["alerts"], "base.closing_missing")
        self.assertTrue(alert)
        self.assertEqual(alert["severity"], "info")
        self.assertEqual(alert["action"], "cmc_base.action_cmc_closing_run")

    def test_closing_missing_absent_when_previous_month_closed(self):
        # "previous month" is relative to today, not to the selected period
        today = fields.Date.context_today(self._cockpit())
        prev = today - relativedelta(months=1)
        self._closing(date_utils.start_of(prev, "month"), date_utils.end_of(prev, "month"))
        self.assertIsNone(by_key(self._dash()["alerts"], "base.closing_missing"))


# ======================================================================================================== charts


@tagged("post_install", "-at_install")
class TestDashboardCostChart(DashboardCase):
    """Chart 1 "Costi per natura": top 8 elements + "Altri", hidden without classified cost."""

    def test_cost_chart_absent_without_classified_cost(self):
        self._line(30.0, date(2026, 6, 3), behavior=False, element=None)
        self.assertIsNone(by_key(self._dash()["charts"], "base.cost_by_element"))

    def test_cost_chart_top_eight_plus_other(self):
        elements = [self._element(f"Dash el {i:02d}", f"DSH_E{i:02d}", self.company) for i in range(1, 11)]
        for i, element in enumerate(elements, start=1):
            self._classified(10.0 * i, date(2026, 6, 1), element=element)
        chart = by_key(self._dash()["charts"], "base.cost_by_element")
        self.assertTrue(chart)
        self.assertEqual(len(chart["labels"]), 9)
        self.assertEqual(chart["labels"][0], elements[-1].display_name)
        self.assertNotIn(elements[0].display_name, chart["labels"])
        data = chart["datasets"][0]["data"]
        self.assertAlmostEqual(data[0], 100.0)
        self.assertAlmostEqual(data[-1], 30.0, msg="'Altri' = the two smallest elements (10 + 20)")
        self.assertAlmostEqual(sum(data), 550.0)


# ==================================================================================================== empty state


@tagged("post_install", "-at_install")
class TestDashboardGlobalEmpty(DashboardCase):
    def test_global_empty_on_fresh_company(self):
        # no cost and no done run anywhere: the single primary CTA "Avvia la guida configurazione"
        self.assertIs(self._dash()["header"]["global_empty"], True)

    def test_global_empty_false_with_classified_cost(self):
        self._classified(100.0, date(2026, 6, 1))
        self.assertIs(self._dash()["header"]["global_empty"], False)


# ====================================================================================================== security


@tagged("post_install", "-at_install")
class TestDashboardSecurity(DashboardCase):
    """AD-005: no sudo, private hooks not RPC-callable, a role without the models simply sees less."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain_user = new_test_user(
            cls.env,
            login="cmc_dash_plain_user",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )

    def test_private_hooks_not_rpc_callable(self):
        cockpit = self._cockpit()
        for name in PRIVATE_HOOKS:
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(cockpit, name, None)), f"cmc.cockpit.{name} missing")
                with self.assertRaises(AccessError):
                    call_kw(cockpit, name, [YEAR], {})

    def test_get_dashboard_data_rpc_callable_and_readonly(self):
        cockpit = self._cockpit()
        data = call_kw(cockpit, "get_dashboard_data", [YEAR], {})
        self.assertEqual(set(data), PAYLOAD_KEYS)
        self.assertTrue(getattr(type(cockpit).get_dashboard_data, "_readonly", False), "must be @api.readonly")

    def test_get_cockpit_data_keys_unchanged(self):
        # AD-006: the setup guide still reads the old payload
        self.assertEqual(set(self._cockpit().get_cockpit_data()), {"pipeline", "methods", "kpis", "chart"})

    def test_cmc_user_reads_base_contributions(self):
        # the controlling role (non-superuser) actually gets the base figures
        self._full_base_fixture()
        data = self._dash(user=self.cmc_user)
        self.assertTrue(by_key(data["kpis"], "base.cost"))
        self.assertTrue(by_key(data["alerts"], "base.residual"))

    def test_user_without_cmc_group_gets_no_base_contribution(self):
        # AD-005 (omit silently, SC-082, no sudo): a user outside group_cmc_user gets no error and no base.*
        # item (no read on analytic lines / closing / capacity) - never a 0 and never a "—"
        self._full_base_fixture()
        data = self._dash(user=self.plain_user)
        for name in ("kpis", "alerts", "cards", "charts"):
            with self.subTest(section=name):
                self.assertFalse(keys(data[name], "base."), f"{name} leaks base items to a non-controlling user")


# ======================================================================================================== actions


@tagged("post_install", "-at_install")
class TestDashboardActions(TransactionCase):
    """AD-006 / R1: bookmarks of the Cruscotto stay valid; the step guide gets its own action + Configuration item.
    Client actions carry no group_ids in Odoo 19: gating is by menu."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    def test_cockpit_action_opens_controller_dashboard(self):
        action = self.env.ref("cmc_base.action_cmc_cockpit_dashboard")
        self.assertEqual(action._name, "ir.actions.client")
        self.assertEqual(action.tag, "cmc_controller_dashboard")
        self.assertEqual(self.env.ref("cmc_base.menu_cmc_cockpit").action, action)

    def test_setup_guide_action_and_menu(self):
        action = self.env.ref("cmc_base.action_cmc_setup_guide", raise_if_not_found=False)
        self.assertTrue(action, "cmc_base.action_cmc_setup_guide missing")
        self.assertEqual(action._name, "ir.actions.client")
        self.assertEqual(action.tag, "cmc_setup_guide")
        menu = self.env.ref("cmc_base.menu_cmc_setup_guide", raise_if_not_found=False)
        self.assertTrue(menu, "cmc_base.menu_cmc_setup_guide missing")
        self.assertEqual(menu.parent_id, self.env.ref("cmc_base.menu_cmc_config"))
        self.assertEqual(menu.sequence, 5)
        self.assertFalse(menu.group_ids, "the guide item inherits the manager-only gate of Configuration")
        self.assertEqual(menu.action, action)
