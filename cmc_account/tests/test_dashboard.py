"""DASH (UX-002 / DASH-devlead §2 account): the controller dashboard contribution of cmc_account.

Card ``account.reconciliation`` (recording, 20) on the latest DONE ``cmc.coge.reconciliation`` of the current
company (order ``date_to desc, id desc``), alert ``account.unexplained`` (danger, scope run) when its
``unexplained_amount`` is not zero. No KPI. Fixture values are written under the run sentinel: the dashboard reads
stored outputs, recomputing the GL-CA reconciliation engine here would test the engine, not the dashboard.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "account.reconciliation"
ALERT = "account.unexplained"
ACTION = "cmc_account.action_cmc_coge_reconciliation"
MODEL = "cmc.coge.reconciliation"


@tagged("post_install", "-at_install")
class TestDashboardAccount(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Account"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash Account Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        # the dashboard is read by a controller (cmc_user), never by the superuser
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_account_user",
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

    def _make_run(self, date_from, date_to, done=True, company=None, **outputs):
        rec = self.env[MODEL].create({
            "name": "DASH REC %s" % date_to,
            "company_id": (company or self.company).id,
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

    def _assert_run_alert(self, alert, rec):
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "danger")
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])

    # ---- tests --------------------------------------------------------------

    def test_empty_card_without_run(self):
        # DASH account: installed + readable but no reconciliation yet -> empty card with its "Crea" action, no alert
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("recording", 20))
        self.assertIn("recording", [a["key"] for a in data["areas"]])
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("account.")])

    def test_card_reads_latest_done_run(self):
        # DASH account: card = latest DONE reconciliation; an older done one and a newer draft are ignored
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), coge_amount=1.0, coan_amount=2.0)
        latest = self._make_run(date(2026, 1, 1), date(2026, 12, 31), coge_amount=12345.67, coan_amount=12000.11)
        self._make_run(date(2027, 1, 1), date(2027, 3, 31), done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 12345.67)
        self._assert_raw(card, 12000.11)
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual(card["run_date_from"], "2026-01-01")
        self.assertEqual(card["run_date_to"], "2026-12-31")
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertIsInstance(money["value"], str)
        self.assertTrue(money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH account: a done run ending before the period end stays visible (safety net) but is "outdated"
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), coge_amount=10.0, coan_amount=10.0)
        card = self._item(self._data()["cards"], CARD)
        self.assertEqual(card["status"], "outdated")
        self.assertEqual(self._item(self._data({"kind": "month", "ref": "2026-02-10"})["cards"], CARD)["status"], "up_to_date")

    def test_unexplained_alert_fires_on_nonzero(self):
        # DASH account: unexplained GL-CA difference != 0 (sign irrelevant) -> danger alert scoped on the run
        rec = self._make_run(date(2026, 1, 1), date(2026, 12, 31), coge_amount=100.0, coan_amount=90.0, unexplained_amount=-10.0)
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self._assert_run_alert(alert, rec)
        self.assertAlmostEqual(abs(alert["amount"]), 10.0)
        self.assertEqual(self._item(data["cards"], CARD)["status"], "attention")

    def test_no_alert_when_unexplained_zero(self):
        # DASH account: a fully explained reconciliation raises nothing
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), coge_amount=100.0, coan_amount=100.0, unexplained_amount=0.0)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_only_on_latest_done_run(self):
        # DASH account: an older run with a difference and a newer draft with a difference are not alerts
        self._make_run(date(2026, 1, 1), date(2026, 3, 31), unexplained_amount=50.0)
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unexplained_amount=0.0)
        draft = self._make_run(date(2027, 1, 1), date(2027, 3, 31), done=False)
        self.assertEqual(draft.state, "draft")
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_no_read_access_hides_contribution(self):
        # SC-082: a user without read on the reconciliation sees no card, no alert, no error
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), unexplained_amount=5.0)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("account.")])

    def test_other_company_run_not_used(self):
        # DASH account: only the current company: a run of another company leaves this card empty and silent
        self._make_run(date(2026, 1, 1), date(2026, 12, 31), company=self.other_company, coge_amount=999.0, unexplained_amount=7.0)
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
