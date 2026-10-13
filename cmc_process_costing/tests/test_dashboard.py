"""DASH (UX-002 / DASH-devlead §2 process_costing): the controller dashboard contribution of cmc_process_costing.

Card ``process_costing.run`` (costing, 50) on the latest DONE ``cmc.process.run`` of the current company (order
``date_to desc, id desc``): figures transferred out + ending WIP; alert ``process_costing.control_difference``
(danger, scope run) when ``control_difference`` is not zero. No KPI. Outputs are written under the run sentinel:
the dashboard reads stored fields, it does not recompute the cost of production report.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "process_costing.run"
ALERT = "process_costing.control_difference"
ACTION = "cmc_process_costing.action_cmc_process_run"
MODEL = "cmc.process.run"


@tagged("post_install", "-at_install")
class TestDashboardProcessCosting(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Process"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash Process Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_process_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        AA = cls.env["account.analytic.account"]
        cls.center = AA.create(
            {"name": "DASH dept", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        cls.other_center = AA.create(
            {"name": "DASH dept other", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.other_company.id}
        )
        cls.product = cls.env["product.product"].create({"name": "DASH process output"})

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
        # a process run covers exactly one calendar month (constraint)
        company = company or self.company
        rec = self.env[MODEL].create({
            "name": "DASH PROC %s" % date_to,
            "company_id": company.id,
            "center_id": (self.center if company == self.company else self.other_center).id,
            "product_id": self.product.id,
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
        # DASH process_costing: installed + readable, no run -> empty card with action, no alert, no KPI
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 50))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("process_costing.")])

    def test_card_reads_latest_done_run(self):
        # DASH process_costing: card = latest DONE run (date_to desc); older done and newer draft ignored
        self._make_run(date(2026, 11, 1), date(2026, 11, 30), transferred_out_cost=1.0, ending_wip_cost=2.0)
        latest = self._make_run(date(2026, 12, 1), date(2026, 12, 31), transferred_out_cost=88000.4, ending_wip_cost=6500.6)
        self._make_run(date(2027, 1, 1), date(2027, 1, 31), done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 88000.4)
        self._assert_raw(card, 6500.6)
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-12-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertTrue(isinstance(money["value"], str) and money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH process_costing: March run seen in the 2026 year -> "outdated"; in the March month -> up_to_date
        self._make_run(date(2026, 3, 1), date(2026, 3, 31), transferred_out_cost=10.0)
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")
        self.assertEqual(self._item(self._data({"kind": "month", "ref": "2026-03-15"})["cards"], CARD)["status"], "up_to_date")

    def test_control_difference_alert_fires_on_nonzero(self):
        # DASH process_costing: control total broken (!= 0, any sign) -> danger scoped on the run, card attention
        rec = self._make_run(date(2026, 12, 1), date(2026, 12, 31), transferred_out_cost=100.0, control_difference=-0.5)
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "danger")
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])
        self.assertAlmostEqual(abs(alert["amount"]), 0.5)
        self.assertEqual(self._item(data["cards"], CARD)["status"], "attention")

    def test_no_alert_when_control_difference_zero(self):
        # DASH process_costing: a balanced run raises nothing
        self._make_run(date(2026, 12, 1), date(2026, 12, 31), transferred_out_cost=100.0, control_difference=0.0)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_ignores_older_done_and_draft_runs(self):
        # DASH process_costing: a difference on an older done run or a draft is no alert
        self._make_run(date(2026, 11, 1), date(2026, 11, 30), control_difference=3.0)
        self._make_run(date(2026, 12, 1), date(2026, 12, 31), control_difference=0.0)
        self._make_run(date(2027, 1, 1), date(2027, 1, 31), done=False)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on cmc.process.run card and alert are omitted, no error
        self._make_run(date(2026, 12, 1), date(2026, 12, 31), control_difference=3.0)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))

    def test_other_company_run_not_used(self):
        # DASH process_costing: another company's run never feeds the current company's card or alert
        self._make_run(date(2026, 12, 1), date(2026, 12, 31), company=self.other_company, control_difference=3.0)
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
