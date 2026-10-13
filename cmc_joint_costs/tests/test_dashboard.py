"""DASH (UX-002 / DASH-devlead §2 joint_costs): the controller dashboard contribution of cmc_joint_costs.

Card ``joint_costs.run`` (costing, 30) on the latest DONE ``cmc.joint.run`` of the current company (order
``id desc``): figures joint cost + method. No alert, no KPI. The run state is set under the run sentinel: the
dashboard reads stored fields, it does not recompute the allocation.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "joint_costs.run"
ACTION = "cmc_joint_costs.action_cmc_joint_run"
MODEL = "cmc.joint.run"


@tagged("post_install", "-at_install")
class TestDashboardJointCosts(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Joint"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash Joint Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_joint_user",
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

    def _make_run(self, joint_cost, method="physical", date_from=None, date_to=None, done=True, company=None):
        # joint_cost and method are INPUTS of the run: set at creation, then the run is marked computed
        rec = self.env[MODEL].create({
            "name": "DASH JOINT %s" % joint_cost,
            "company_id": (company or self.company).id,
            "joint_cost": joint_cost,
            "method": method,
            "date_from": date_from,
            "date_to": date_to,
        })
        if done:
            rec.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        return rec

    def _assert_raw(self, card, value):
        raws = [f["raw"] for f in card["figures"] if isinstance(f["raw"], (int, float))]
        self.assertTrue(
            any(abs(raw - value) < 0.005 for raw in raws), "figure %s not on card %s (raws %s)" % (value, card["key"], raws)
        )

    # ---- tests --------------------------------------------------------------

    def test_empty_card_without_run(self):
        # DASH joint_costs: installed + readable, no joint run -> empty card with action; never an alert or KPI
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 30))
        self.assertFalse([a for a in data["alerts"] if a["key"].startswith("joint_costs.")])
        self.assertFalse([k for k in data["kpis"] if k["key"].startswith("joint_costs.")])

    def test_card_reads_latest_done_run(self):
        # DASH joint_costs: card = latest DONE run by id (joint cost + method); older done and newer draft ignored
        self._make_run(11.0)
        latest = self._make_run(24680.5, method="nrv", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
        self._make_run(99.0, done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 24680.5)
        method_label = dict(self.env[MODEL]._fields["method"]._description_selection(self.env))["nrv"]
        self.assertIn(method_label, [f["value"] for f in card["figures"]])
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-01-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")
        money = next(f for f in card["figures"] if f["kind"] == "monetary")
        self.assertTrue(isinstance(money["value"], str) and money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH joint_costs: a dated run ending before the period end -> "outdated"
        self._make_run(10.0, date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")

    def test_undated_run_is_up_to_date(self):
        # DASH helper rule: an undated artefact cannot be stale -> up_to_date, no run dates, in_period True
        self._make_run(10.0)
        card = self._item(self._data()["cards"], CARD)
        self.assertEqual(card["status"], "up_to_date")
        self.assertIsNone(card["run_date_to"])
        self.assertTrue(card["in_period"])  # undated artefact: always in period (cmc_base _cmc_dashboard_card)

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on cmc.joint.run the card is omitted, no error
        self._make_run(10.0)
        self.env["ir.model.access"].search([("model_id.model", "=", MODEL)]).write({"perm_read": False})
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))

    def test_other_company_run_not_used(self):
        # DASH joint_costs: another company's run never fills the current company's card
        self._make_run(10.0, company=self.other_company)
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "empty")
