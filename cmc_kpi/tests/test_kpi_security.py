"""AD-115 security of cmc_kpi, every case executed AS the role it is about.

- responsible (group_cmc_responsible only): reads the KPIs and KPI values of the responsibility centers he owns;
  owner_ops owns yield_pct and cycle_time, owner_sales owns otd; no access to runs or configuration writes.
- CMC user: reads every KPI, computes runs (no unlink), types targets; cannot edit the library.
- CMC manager: maintains the library and the scorecards.
- internal user without CMC group: AccessError.
- multi-company: global rule company_id parent_of company_ids; codes unique per company only.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import JAN, KpiCase


@tagged("post_install", "-at_install")
class TestKpiSecurity(KpiCase):
    def test_responsible_sees_own_kpis_only(self):
        # KPI-03: the owner of a responsibility center sees the KPIs of his center
        lib = self._library()
        visible = self.env["cmc.kpi"].with_user(self.owner_ops).search([])
        self.assertEqual(visible, lib["yield_pct"] | lib["cycle_time"])
        self.assertEqual(self.env["cmc.kpi"].with_user(self.owner_sales).search([]), lib["otd"])

    def test_responsible_cannot_read_other_kpi(self):
        # KPI-03: another center's KPI is not readable
        lib = self._library()
        with self.assertRaises(AccessError):
            lib["otd"].with_user(self.owner_ops).read(["name"])

    def test_responsible_sees_own_values_only(self):
        # KPI-03: the measured values follow the KPI owner
        lib = self._library()
        run = self._run(JAN)
        values = self.env["cmc.kpi.value"].with_user(self.owner_sales).search([("run_id", "=", run.id)])
        self.assertEqual(values.kpi_id, lib["otd"])
        self.assertAlmostEqual(values.with_user(self.owner_sales).value, 75.0, places=4)

    def test_responsible_cannot_run_or_configure(self):
        # AD-115: the responsible group reads; it does not compute or configure
        with self.assertRaises(AccessError):
            self._run(JAN, user=self.owner_ops, compute=False)
        with self.assertRaises(AccessError):
            self.env["cmc.kpi"].with_user(self.owner_ops).create(
                {"name": "x", "code": "resp_kpi", "source_type": "measure", "driver_id": self.good.id}
            )

    def test_user_computes_a_run(self):
        # AD-115: the controlling user creates and computes the monthly run himself
        lib = self._library()
        run = self._run(JAN, user=self.user)
        self.assertEqual(run.state, "done")
        self.assertAlmostEqual(self._val(run, lib["otd"], JAN).value, 75.0, places=4)
        with self.assertRaises(AccessError):
            run.with_user(self.user).unlink()

    def test_user_sees_all_kpis_but_cannot_edit_library(self):
        # AD-115: the library is configuration of the manager
        lib = self._library()
        self.assertEqual(len(self.env["cmc.kpi"].with_user(self.user).search([])), len(lib))
        with self.assertRaises(AccessError):
            lib["otd"].with_user(self.user).write({"target_value": 80.0})

    def test_user_types_targets(self):
        # AD-104: targets are typed by the controlling user on a version
        lib = self._library()
        version = self._target_version([])
        target = self.env["cmc.kpi.target"].with_user(self.user).create(
            {"version_id": version.id, "kpi_id": lib["otd"].id, "date": JAN, "target_value": 97.0}
        )
        self.assertEqual(target.target_value, 97.0)

    def test_manager_maintains_library_and_scorecards(self):
        # AD-115: the manager creates KPIs and scorecards
        kpi = self.env["cmc.kpi"].with_user(self.manager).create(
            {"name": "Mgr KPI", "code": "mgr_kpi", "source_type": "measure", "driver_id": self.good.id}
        )
        scorecard = self.env["cmc.kpi.scorecard"].with_user(self.manager).create({
            "name": "Mgr scorecard", "perspective_ids": [(0, 0, {"perspective": "financial", "weight": 100.0})],
            "line_ids": [(0, 0, {"kpi_id": kpi.id, "weight": 100.0})],
        })
        self.assertTrue(scorecard.line_ids)

    def test_results_read_only_for_user(self):
        # AD-113: values are engine output, also for the manager
        lib = self._library()
        run = self._run(JAN)
        row = self._val(run, lib["otd"], JAN)
        for user in (self.user, self.manager):
            with self.assertRaises(AccessError):
                row.with_user(user).write({"value": 1.0})

    def test_outsider_has_no_access(self):
        # AD-115: no CMC group, no KPI
        self._library()
        for model in ("cmc.kpi", "cmc.kpi.value", "cmc.kpi.run", "cmc.kpi.scorecard"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.outsider).search([])

    def test_company_rules(self):
        # AD-115: a company B library is invisible to company A users and absent from company A runs
        lib = self._library()
        company_b = self.env["res.company"].create({"name": "KPI company B"})
        user_b = new_test_user(self.env, login="kpi_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                               company_id=company_b.id, company_ids=[Command.set(company_b.ids)])
        kpi_b = self.env["cmc.kpi"].create({"name": "B otd", "code": "otd", "company_id": company_b.id,
                                            "source_type": "measure", "driver_id": self.good.id})
        self.assertEqual(self.env["cmc.kpi"].with_user(user_b).search([]), kpi_b)
        self.assertNotIn(kpi_b, self.env["cmc.kpi"].with_user(self.user).search([]))
        run = self._run(JAN)
        self.assertNotIn(kpi_b, run.value_ids.kpi_id)
        self.assertEqual(run.value_ids.kpi_id, self.env["cmc.kpi"].browse([k.id for k in lib.values()]))
        with self.assertRaises(AccessError):
            run.with_user(user_b).read(["name"])

    def test_global_rules_not_noupdate(self):
        # AD-115: record rules outside noupdate so a domain fix reaches installed databases
        rules = self.env["ir.rule"].search([("model_id.model", "in", ["cmc.kpi", "cmc.kpi.value", "cmc.kpi.run"]), ("global", "=", True)])
        self.assertEqual(set(rules.mapped("model_id.model")), {"cmc.kpi", "cmc.kpi.value", "cmc.kpi.run"})
        data = self.env["ir.model.data"].search([("model", "=", "ir.rule"), ("res_id", "in", rules.ids)])
        self.assertTrue(data)
        self.assertFalse(any(data.mapped("noupdate")))
