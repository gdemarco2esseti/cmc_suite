"""DASH (UX-002 / DASH-devlead §2 full_costing): the controller dashboard contribution of cmc_full_costing.

Card ``full_costing.run`` (costing, 10) on the latest DONE ``cmc.costing.run`` of the current company (order
``date_to desc, id desc``), figure = unabsorbed cost; alert ``full_costing.unabsorbed`` (warning, scope run) when
``unabsorbed_cost`` is not zero. No KPI. Outputs are written under the run sentinel: the dashboard reads stored
fields, it does not recompute the costing.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "full_costing.run"
ALERT = "full_costing.unabsorbed"
ACTION = "cmc_full_costing.action_cmc_costing_run"
MODEL = "cmc.costing.run"


@tagged("post_install", "-at_install")
class TestDashboardFullCosting(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Full Costing"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash Full Costing Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_fc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )
        # a costing run reads a computed closing covering its window (constraint)
        cls.closing = cls._closing(cls.company, date(2026, 1, 1), date(2027, 12, 31))
        cls.other_closing = cls._closing(cls.other_company, date(2026, 1, 1), date(2026, 12, 31))

    @classmethod
    def _closing(cls, company, date_from, date_to):
        closing = cls.env["cmc.closing.run"].create(
            {"date_from": date_from, "date_to": date_to, "method": "direct", "company_id": company.id}
        )
        closing.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        return closing

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

    def _make_run(self, date_from, date_to, done=True, company=None, **outputs):
        company = company or self.company
        closing = self.closing if company == self.company else self.other_closing
        rec = self.env[MODEL].create({
            "name": "DASH FC %s" % date_to,
            "company_id": company.id,
            "closing_run_id": closing.id,
            "date_from": date_from,
            "date_to": date_to,
        })
        if done:
            rec.with_context(cmc_run_state=CMC_RUN_STATE).write({**outputs, "state": "done"})
        return rec

    def _assert_raw(self, card, value):
        raws = [f["raw"] for f in card["figures"] if isinstance(f["raw"], (int, float))]
        self.assertTrue(
            any(abs(raw - value) < 0.005 for raw in raws), "figure %s not on card %s (raws %s)" % (value, card["key"], raws)
        )

    # ---- tests --------------------------------------------------------------

    def test_empty_card_without_run(self):
        # DASH full_costing: installed + readable, no costing run -> empty card with its action, no alert, no KPI
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 10))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("full_costing.")])

    def test_card_reads_latest_done_run(self):
        # DASH full_costing: card = latest DONE run (date_to desc); older done and newer draft are ignored
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), unabsorbed_cost=1.0)
        latest = self._make_run(date(2026, 1, 1), date(2026, 12, 31), unabsorbed_cost=0.0)
        self._make_run(date(2027, 1, 1), date(2027, 6, 30), done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 0.0)
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-01-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertTrue(isinstance(money["value"], str) and money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH full_costing: a done run ending before the period end is shown "outdated" (safety net)
        self._make_run(date(2026, 1, 1), date(2026, 6, 30))
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")

    def test_unabsorbed_alert_fires_on_nonzero(self):
        # DASH full_costing: unabsorbed cost != 0 on the latest done run -> warning scoped on that run, card attention
        rec = self._make_run(date(2026, 1, 1), date(2026, 12, 31), unabsorbed_cost=4321.5)
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])
        self.assertAlmostEqual(alert["amount"], 4321.5)
        card = self._item(data["cards"], CARD)
        self._assert_raw(card, 4321.5)
        self.assertEqual(card["status"], "attention")

    def test_no_alert_when_unabsorbed_zero(self):
        # DASH full_costing: fully absorbed capacity raises nothing
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unabsorbed_cost=0.0)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_ignores_older_done_and_draft_runs(self):
        # DASH full_costing: only the latest done run counts; an older one with unabsorbed cost is history
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), unabsorbed_cost=80.0)
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unabsorbed_cost=0.0)
        self._make_run(date(2027, 1, 1), date(2027, 6, 30), done=False)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on cmc.costing.run the card and the alert are omitted, no error
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unabsorbed_cost=10.0)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))

    def test_other_company_run_not_used(self):
        # DASH full_costing: a run of another company never feeds the current company's card or alert
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), company=self.other_company, unabsorbed_cost=55.0)
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
