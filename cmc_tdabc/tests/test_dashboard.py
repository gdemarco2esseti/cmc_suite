"""DASH (UX-002 / DASH-devlead §2 tdabc): the controller dashboard contribution of cmc_tdabc.

Card ``tdabc.run`` (costing, 40) on the latest DONE ``cmc.tdabc.run`` of the current company (order ``id desc``),
figure = capacity use %; figures read the ``cmc.tdabc.capacity.result`` rows of that run for scenario
``timesheet`` when present, else ``manual``. Alert ``tdabc.over_capacity`` (warning, scope run) when that run has
over-capacity results; KPI ``tdabc.capacity`` (slot capacity, priority 10 = wins over ABC 20) = unused capacity
cost. Results are created under the run sentinel: the dashboard reads stored rows, it does not run the engine.
"""

from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
CARD = "tdabc.run"
ALERT = "tdabc.over_capacity"
KPI = "tdabc.capacity"
ACTION = "cmc_tdabc.action_cmc_tdabc_run"
MODEL = "cmc.tdabc.run"
Y_FROM, Y_TO = date(2026, 1, 1), date(2026, 12, 31)


@tagged("post_install", "-at_install")
class TestDashboardTdabc(TransactionCase):
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash TDABC"})
        cls.other_company = cls.env["res.company"].create({"name": "CMC Dash TDABC Other"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env,
            login="cmc_dash_tdabc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )
        Resource = cls.env["cmc.tdabc.resource"]
        cls.res_a = Resource.create({"name": "DASH press", "cost": 1000.0, "theoretical_time": 1000.0, "company_id": cls.company.id})
        cls.res_b = Resource.create({"name": "DASH lathe", "cost": 500.0, "theoretical_time": 500.0, "company_id": cls.company.id})
        cls.res_other = Resource.create(
            {"name": "DASH other", "cost": 100.0, "theoretical_time": 100.0, "company_id": cls.other_company.id}
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

    def _make_run(self, results=(), date_from=Y_FROM, date_to=Y_TO, done=True, company=None):
        """results: iterable of (scenario, resource, unused_cost, capacity_use_pct, over_capacity)."""
        rec = self.env[MODEL].create({
            "name": "DASH TDABC %s" % date_to,
            "company_id": (company or self.company).id,
            "date_from": date_from,
            "date_to": date_to,
        })
        engine = self.env["cmc.tdabc.capacity.result"].with_context(cmc_run_state=CMC_RUN_STATE)
        engine.create([
            {
                "run_id": rec.id, "scenario": scenario, "resource_id": resource.id, "unused_cost": unused,
                "capacity_use_pct": use_pct, "over_capacity": over,
            }
            for scenario, resource, unused, use_pct, over in results
        ])
        if done:
            rec.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        return rec

    def _assert_raw(self, card, value):
        raws = [f["raw"] for f in card["figures"] if isinstance(f["raw"], (int, float))]
        self.assertTrue(
            any(abs(raw - value) < 0.005 for raw in raws), "figure %s not on card %s (raws %s)" % (value, card["key"], raws)
        )

    def _remove_read(self, model):
        self.env["ir.model.access"].search([("model_id.model", "=", model)]).write({"perm_read": False})

    # ---- card ---------------------------------------------------------------

    def test_empty_card_without_run(self):
        # DASH tdabc: installed + readable, no run -> empty card with action, no alert, KPI omitted (never had data)
        data = self._data()
        card = self._item(data["cards"], CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["action"], ACTION)
        self.assertEqual((card["area"], card["sequence"]), ("costing", 40))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))

    def test_card_reads_latest_done_run_timesheet_scenario(self):
        # DASH tdabc: card = latest DONE run by id; capacity use % read from scenario "timesheet" when present
        self._make_run([("manual", self.res_a, 1.0, 11.0, False)])
        latest = self._make_run([
            ("manual", self.res_a, 300.0, 70.0, False),
            ("timesheet", self.res_a, 150.0, 85.0, False),
        ])
        self._make_run([("timesheet", self.res_a, 9.0, 99.0, False)], done=False)
        card = self._item(self._data()["cards"], CARD)
        self._assert_raw(card, 85.0)
        self.assertIn(latest.name, card["subtitle"])
        self.assertEqual((card["run_date_from"], card["run_date_to"]), ("2026-01-01", "2026-12-31"))
        self.assertTrue(card["in_period"])
        self.assertEqual(card["status"], "up_to_date")

    def test_card_manual_scenario_without_timesheet(self):
        # DASH tdabc: without timesheet results the figures fall back to scenario "manual"
        self._make_run([("manual", self.res_a, 300.0, 70.0, False)])
        self._assert_raw(self._item(self._data()["cards"], CARD), 70.0)

    def test_card_outdated_when_run_ends_before_period_end(self):
        # DASH tdabc: a run ending before the period end is shown "outdated"
        self._make_run([("manual", self.res_a, 1.0, 50.0, False)], date_to=date(2026, 3, 31))
        self.assertEqual(self._item(self._data()["cards"], CARD)["status"], "outdated")

    # ---- alert --------------------------------------------------------------

    def test_over_capacity_alert(self):
        # DASH tdabc: resources over capacity on the latest done run -> warning scoped on the run, count = resources
        rec = self._make_run([
            ("manual", self.res_a, 0.0, 120.0, True),
            ("manual", self.res_b, 50.0, 90.0, False),
        ])
        data = self._data()
        alert = self._item(data["alerts"], ALERT)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], ACTION)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual([list(leaf) for leaf in alert["domain"]], [["id", "=", rec.id]])
        self.assertEqual(alert["count"], 1)
        self.assertEqual(self._item(data["cards"], CARD)["status"], "attention")

    def test_no_alert_without_over_capacity(self):
        # DASH tdabc: no resource over capacity -> no alert
        self._make_run([("manual", self.res_a, 10.0, 90.0, False)])
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    def test_alert_ignores_older_done_and_draft_runs(self):
        # DASH tdabc: over capacity on an older done run or on a draft is no alert
        self._make_run([("manual", self.res_a, 0.0, 130.0, True)])
        self._make_run([("manual", self.res_a, 10.0, 90.0, False)])
        self._make_run([("manual", self.res_a, 0.0, 140.0, True)], done=False)
        self.assertIsNone(self._item(self._data()["alerts"], ALERT))

    # ---- KPI ----------------------------------------------------------------

    def test_kpi_capacity_timesheet_scenario(self):
        # DASH KPI 4: TDABC feeds slot "capacity", priority 10, unused cost summed on scenario "timesheet"
        self._make_run([
            ("manual", self.res_a, 900.0, 10.0, False),
            ("manual", self.res_b, 100.0, 10.0, False),
            ("timesheet", self.res_a, 100.0, 80.0, False),
            ("timesheet", self.res_b, 50.0, 80.0, False),
        ])
        kpi = self._item(self._data()["kpis"], KPI)
        self.assertIsNotNone(kpi)
        self.assertEqual(kpi["slot"], "capacity")
        self.assertEqual(kpi["priority"], 10)
        self.assertAlmostEqual(kpi["raw"], 150.0)

    def test_kpi_capacity_ignores_over_capacity_rows(self):
        # over-capacity rows (negative unused cost) are the separate alert, never netted into the unused capacity
        self._make_run([
            ("manual", self.res_a, -500.0, 120.0, True),
            ("manual", self.res_b, 100.0, 80.0, False),
        ])
        data = self._data()
        self.assertAlmostEqual(self._item(data["kpis"], KPI)["raw"], 100.0)
        self.assertEqual(self._item(data["alerts"], ALERT)["label"], "1 resource over capacity")

    def test_kpi_capacity_manual_scenario(self):
        # DASH KPI 4: without timesheet results the unused cost comes from scenario "manual"
        self._make_run([("manual", self.res_a, 400.0, 60.0, False), ("manual", self.res_b, 25.0, 60.0, False)])
        self.assertAlmostEqual(self._item(self._data()["kpis"], KPI)["raw"], 425.0)

    def test_kpi_tdabc_wins_over_abc(self):
        # DASH KPI slot capacity: TDABC (prio 10) beats ABC (prio 20) when both have data
        if "cmc.abc.run" not in self.env:
            self.skipTest("cmc_abc not installed")
        abc = self.env["cmc.abc.run"].create({"company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO})
        abc.with_context(cmc_run_state=CMC_RUN_STATE).write({"unused_capacity_cost": 777.0, "state": "done"})
        self._make_run([("manual", self.res_a, 400.0, 60.0, False)])
        kpis = [k for k in self._data()["kpis"] if k["slot"] == "capacity"]
        self.assertEqual([k["key"] for k in kpis], [KPI])
        self.assertAlmostEqual(kpis[0]["raw"], 400.0)

    def test_kpi_abc_wins_when_tdabc_unreadable(self):
        # SC-082: TDABC unreadable for the user -> the capacity slot falls back to ABC, TDABC keys all absent
        if "cmc.abc.run" not in self.env:
            self.skipTest("cmc_abc not installed")
        abc = self.env["cmc.abc.run"].create({"company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO})
        abc.with_context(cmc_run_state=CMC_RUN_STATE).write({"unused_capacity_cost": 777.0, "state": "done"})
        self._make_run([("manual", self.res_a, 400.0, 60.0, True)])
        self._remove_read(MODEL)
        data = self._data()
        kpis = [k for k in data["kpis"] if k["slot"] == "capacity"]
        self.assertEqual([k["key"] for k in kpis], ["abc.capacity"])
        self.assertAlmostEqual(kpis[0]["raw"], 777.0)

    # ---- access / company ---------------------------------------------------

    def test_no_read_access_hides_contribution(self):
        # SC-082: without read on cmc.tdabc.run card, alert and KPI are omitted (not 0, not "—"), no error
        self._make_run([("manual", self.res_a, 400.0, 120.0, True)])
        self._remove_read(MODEL)
        data = self._data()
        self.assertIsNone(self._item(data["cards"], CARD))
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))

    def test_no_read_access_on_results_hides_contribution(self):
        # AD-005: the module checks read on every model it reads, capacity results included
        self._make_run([("manual", self.res_a, 400.0, 120.0, True)])
        self._remove_read("cmc.tdabc.capacity.result")
        data = self._data()
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))

    def test_other_company_run_not_used(self):
        # DASH tdabc: another company's run feeds neither card, nor alert, nor KPI
        self._make_run([("manual", self.res_other, 400.0, 120.0, True)], company=self.other_company)
        data = self._data()
        self.assertEqual(self._item(data["cards"], CARD)["status"], "empty")
        self.assertIsNone(self._item(data["alerts"], ALERT))
        self.assertIsNone(self._item(data["kpis"], KPI))
