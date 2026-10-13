"""DASH (UX-002 / DASH-devlead §2 standard_cost): the controller dashboard contribution of cmc_standard_cost.

Card ``standard_cost.variance`` (costing, 60) on the latest DONE ``cmc.standard.variance.run`` of the current
company (model order ``date_to desc, id desc``): figure = variance total; alert ``standard_cost.skipped``
(warning, scope run, count = ``skipped_count``) when MOs of the period had no standard card. No KPI. Outputs are
written under the run sentinel: the dashboard reads stored fields, it does not recompute MO variances.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "standard_cost.variance"
ALERT = "standard_cost.skipped"
ACTION = "cmc_standard_cost.action_cmc_standard_variance_run"
MODEL = "cmc.standard.variance.run"


@tagged("post_install", "-at_install")
class TestDashboardStandardCost(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Standard"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash Standard Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_std_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )
        cls.version = cls._version(cls.company)
        cls.other_version = cls._version(cls.other_company)

    @classmethod
    def _version(cls, company):
        # a variance run reads a standard version spanning its period; approval is not re-run here
        # (it would publish rates): the version is marked approved under the sentinel
        version = cls.env["cmc.plan.version"].create({
            "name": "DASH STD %s" % company.name, "version_type": "standard", "company_id": company.id,
            "date_from": date(2025, 1, 1), "date_to": date(2027, 12, 31),
        })
        version.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        return version

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
        rec = self.env[MODEL].create({
            "name": "DASH VAR %s" % date_to,
            "company_id": company.id,
            "version_id": (self.version if company == self.company else self.other_version).id,
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
        # DASH standard_cost: installed + readable, no variance run -> empty card with action, no alert, no KPI
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 60))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("standard_cost.")])

    def test_card_reads_latest_done_run(self):
        # DASH standard_cost: card = latest DONE run (date_to desc); older done and newer draft ignored
        self._make_run(date(2026, 1, 1), date(2026, 6, 30), variance_total=1.0)
        latest = self._make_run(date(2026, 7, 1), date(2026, 12, 31), variance_total=-4321.09)
        self._make_run(date(2027, 1, 1), date(2027, 3, 31), done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, -4321.09)
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-07-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertTrue(isinstance(money["value"], str) and money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH standard_cost: a run ending at June seen in the 2026 year -> "outdated"
        self._make_run(date(2026, 1, 1), date(2026, 6, 30), variance_total=5.0)
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")

    def test_skipped_alert_counts_mos_without_card(self):
        # DASH standard_cost: MOs without standard card on the latest done run -> warning, count = skipped_count
        rec = self._make_run(date(2026, 7, 1), date(2026, 12, 31), variance_total=10.0, skipped_count=3)
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["count"], 3)
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])
        self.assertEqual(self._item(data["cards"], CARD)["status"], "attention")

    def test_no_alert_when_nothing_skipped(self):
        # DASH standard_cost: every MO had a card -> no alert
        self._make_run(date(2026, 7, 1), date(2026, 12, 31), skipped_count=0)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_ignores_older_done_and_draft_runs(self):
        # DASH standard_cost: skipped MOs on an older done run or a draft are no alert
        self._make_run(date(2026, 1, 1), date(2026, 6, 30), skipped_count=4)
        self._make_run(date(2026, 7, 1), date(2026, 12, 31), skipped_count=0)
        self._make_run(date(2027, 1, 1), date(2027, 3, 31), done=False)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on the variance run card and alert are omitted, no error
        self._make_run(date(2026, 7, 1), date(2026, 12, 31), skipped_count=2)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))

    def test_other_company_run_not_used(self):
        # DASH standard_cost: another company's run never feeds the current company's card or alert
        self._make_run(date(2026, 7, 1), date(2026, 12, 31), company=self.other_company, skipped_count=2)
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
