"""DASH (UX-002 / DASH-devlead §2 abc): the controller dashboard contribution of cmc_abc.

Card ``abc.run`` (costing, 20) on the latest DONE ``cmc.abc.run`` of the current company (order ``id desc``):
figures object cost + sustaining (never the KPI figure); alert ``abc.unassigned`` (warning, scope run) when
``unassigned_cost`` > 0; KPI ``abc.capacity`` (slot capacity, priority 20) = unused capacity cost of the latest done
run IN the period ("—" i.e. raw None when runs exist only outside it; absent when the company never had a run).
Outputs are written under the run sentinel: the dashboard reads stored fields, it does not run the ABC engine.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "abc.run"
ALERT = "abc.unassigned"
KPI = "abc.capacity"
ACTION = "cmc_abc.action_cmc_abc_run"
MODEL = "cmc.abc.run"


@tagged("post_install", "-at_install")
class TestDashboardAbc(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash ABC"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash ABC Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_abc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )

    # ---- helpers ------------------------------------------------------------

    def _data(self, period=PERIOD):
        return (
            self.env["cmc.cockpit"]
            .with_user(self.user)
            .with_context(allowed_company_ids=[self.company.id])
            .get_dashboard_data(period)
        )

    @staticmethod
    def _item(items, key):
        return next((item for item in items if item["key"] == key), None)

    def _make_run(self, date_from=None, date_to=None, done=True, company=None, **outputs):
        rec = self.env[MODEL].create({
            "name": "DASH ABC %s" % date_to,
            "company_id": (company or self.company).id,
            "date_from": date_from,
            "date_to": date_to,
        })
        if done:
            rec.with_context(cmc_run_state=CMC_RUN_STATE).write({**outputs, "state": "done"})
        return rec

    @staticmethod
    def _raws(card):
        return [f["raw"] for f in card["figures"] if isinstance(f["raw"], (int, float))]

    def _assert_raw(self, card, value):
        raws = self._raws(card)
        self.assertTrue(
            any(abs(raw - value) < 0.005 for raw in raws), "figure %s not on card %s (raws %s)" % (value, card["key"], raws)
        )

    # ---- card ---------------------------------------------------------------

    def test_empty_card_without_run(self):
        # DASH abc: installed + readable, no ABC run -> empty card with action, no alert, KPI omitted (never had data)
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 20))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))

    def test_card_reads_latest_done_run(self):
        # DASH abc: card = latest DONE run by id; an older done run and a newer draft are ignored
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), total_object_cost=1.0, sustaining_cost=2.0)
        latest = self._make_run(
            date(2026, 1, 1), date(2026, 12, 31), total_object_cost=50000.25, sustaining_cost=7000.75, unused_capacity_cost=1234.5
        )
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 50000.25)
        self._assert_raw(card, 7000.75)
        # UX-002: a card never repeats a headline KPI figure (unused capacity is KPI 4)
        self.assertFalse(any(abs(raw - 1234.5) < 0.005 for raw in self._raws(card)))
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-01-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertTrue(isinstance(money["value"], str) and money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH abc: a done run ending before the period end is still shown, chip "outdated"
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), total_object_cost=10.0)
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")

    # ---- alert --------------------------------------------------------------

    def test_unassigned_alert_fires_when_positive(self):
        # DASH abc: unassigned cost > 0 on the latest done run -> warning scoped on that run, card attention
        rec = self._make_run(date(2026, 1, 1), date(2026, 12, 31), unassigned_cost=333.3)
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])
        self.assertAlmostEqual(alert["amount"], 333.3)
        self.assertEqual(self._item(data["cards"], CARD)["status"], "attention")

    def test_no_alert_when_unassigned_zero(self):
        # DASH abc: nothing unassigned -> no alert
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unassigned_cost=0.0)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_ignores_older_done_and_draft_runs(self):
        # DASH abc: an older done run with unassigned cost is history, a draft is no alert
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unassigned_cost=99.0)
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unassigned_cost=0.0)
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), done=False)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    # ---- KPI ----------------------------------------------------------------

    def test_kpi_capacity_from_run_in_period(self):
        # DASH KPI 4: ABC feeds slot "capacity" with priority 20 from the latest done run in the period
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unused_capacity_cost=2500.0)
        kpi = self._item(self._data()["kpis"], KPI)
        self.assertIsNotNone(kpi)
        self.assertEqual(kpi["slot"], "capacity")
        self.assertEqual(kpi["priority"], 20)
        self.assertAlmostEqual(kpi["raw"], 2500.0)

    def test_kpi_dash_when_runs_only_outside_period(self):
        # UX-002: data exists but none in the period -> KPI shown with raw None / "—", never 0
        self._make_run(date(2025, 1, 1), date(2025, 12, 31), unused_capacity_cost=800.0)
        data = self._data()
        kpi = self._item(data["kpis"], KPI)
        self.assertIsNotNone(kpi)
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")
        # the card still shows the out-of-period run (safety net)
        card = self._item(data["cards"], CARD)
        self.assertFalse(card["in_period"])
        self.assertEqual(card["status"], "outdated")

    def test_kpi_uses_in_period_run_when_latest_is_outside(self):
        # DASH abc: latest done run is out of the period -> KPI falls back to the latest done run IN the period
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unused_capacity_cost=600.0)
        self._make_run(date(2025, 1, 1), date(2025, 12, 31), unused_capacity_cost=900.0)
        kpi = self._item(self._data()["kpis"], KPI)
        self.assertAlmostEqual(kpi["raw"], 600.0)

    # ---- access / company ---------------------------------------------------

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on cmc.abc.run card, alert and KPI are all omitted (not 0, not "—"), no error
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unassigned_cost=5.0, unused_capacity_cost=5.0)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))

    def test_other_company_run_not_used(self):
        # DASH abc: another company's run feeds neither card, nor alert, nor KPI of the current company
        self._make_run(
            date(2026, 1, 1), date(2026, 12, 31), company=self.other_company, unassigned_cost=5.0, unused_capacity_cost=5.0
        )
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))
