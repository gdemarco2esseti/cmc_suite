"""WP-15 / AD-115 - project control roles act with their own user (with_user), two companies.

CONTRACT (part, see ``evm_common``)
- A ``group_cmc_user`` (also project user) builds a DRAFT baseline (version, rate card, estimate lines,
  risks), runs the plan, raises change requests and computes EVM status runs: figures identical to the
  manager's. They cannot approve a baseline nor a change request (AccessError) and cannot write the engine
  rows (baseline lines, task lines, curve rows: AccessError).
- ``group_cmc_manager`` approves the baseline and the change request.
- Without a CMC group: no access to the EVM models (AccessError).
- A CMC user of company B sees none of company A's baselines rows, risks, change requests or runs.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .evm_common import MAR_END, EvmCase


@tagged("post_install", "-at_install")
class TestEvmSecurity(EvmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups_user = "base.group_user,project.group_project_user,cmc_base.group_cmc_user"
        cls.user_a = new_test_user(
            cls.env, login="wp15_cmc_a", groups=groups_user,
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.user_b = new_test_user(
            cls.env, login="wp15_cmc_b", groups=groups_user,
            company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.manager = new_test_user(
            cls.env, login="wp15_cmc_manager", groups="base.group_user,project.group_project_manager,cmc_base.group_cmc_manager",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="wp15_outsider", groups="base.group_user,project.group_project_user",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )

    def _manager_approved_baseline_built_by_user(self):
        version = self._baseline(approve=False, env=self._env_a(self.user_a))
        version.action_cmc_evm_plan()
        version.with_user(self.manager).action_approve()
        return version

    def test_cmc_user_builds_draft_baseline_but_cannot_approve(self):
        # PRJ-01/02 as the controller who prepares the estimate; approval is the manager's (AD-104)
        version = self._baseline(approve=False, env=self._env_a(self.user_a))
        version.action_cmc_evm_plan()
        self.assertAlmostEqual(version.estimate_expected, 32200.0, places=2)
        with self.assertRaises(AccessError):
            version.action_approve()
        version.with_user(self.manager).action_approve()
        self.assertEqual(version.state, "done")
        self.assertAlmostEqual(version.bac, 32200.0, places=2)

    def test_cmc_user_computes_status_report(self):
        # PRJ-04 as the controller who presses Compute: same figures as the manager
        baseline = self._manager_approved_baseline_built_by_user()
        run = self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0}, env=self._env_a(self.user_a))
        self.assertEqual(run.state, "done")
        self.assertRecordValues(run, [{"pv": 20650.0, "ev": 16800.0, "ac": 17000.0, "eac": 32583.33}])

    def test_cmc_user_cannot_write_engine_rows(self):
        # AD-001: baseline PV, task lines and curve rows are written by the engine only
        baseline = self._manager_approved_baseline_built_by_user()
        run = self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0}, env=self._env_a(self.user_a))
        with self.assertRaises(AccessError):
            run.curve_ids[:1].with_user(self.user_a).write({"ac": 1.0})
        with self.assertRaises(AccessError):
            run.task_line_ids[:1].with_user(self.user_a).write({"ev": 1.0})
        with self.assertRaises(AccessError):
            baseline.baseline_line_ids[:1].with_user(self.user_a).write({"planned_value": 1.0})

    def test_change_request_raised_by_user_approved_by_manager(self):
        # PRJ-03: anyone in control raises a change, only the manager re-baselines
        baseline = self._manager_approved_baseline_built_by_user()
        cr = self._env_a(self.user_a)["cmc.evm.change.request"].create({
            "name": "CR user", "baseline_id": baseline.id, "task_id": self.t2.id, "date": date(2026, 4, 10), "cost_impact": 1500.0,
        })
        with self.assertRaises(AccessError):
            cr.action_approve()
        self.assertEqual(cr.state, "draft")
        cr.with_user(self.manager).action_approve()
        self.assertEqual(cr.state, "approved")
        self.assertAlmostEqual(cr.new_baseline_id.bac, 33700.0, places=2)

    def test_outsider_has_no_access(self):
        # AD-115: project control is a CMC function
        env = self._env_a(self.outsider)
        for model in ("cmc.evm.run", "cmc.evm.risk", "cmc.evm.estimate.line", "cmc.evm.change.request", "cmc.evm.curve"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                env[model].search([])

    def test_other_company_user_sees_nothing(self):
        # AD-115: company perimeter on every WP-15 model
        baseline = self._baseline()
        self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0})
        self.env["cmc.evm.change.request"].create({
            "name": "CR A", "baseline_id": baseline.id, "task_id": self.t2.id, "date": date(2026, 4, 10), "cost_impact": 10.0,
        })
        env_b = self.env(user=self.user_b, context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        for model in ("cmc.evm.run", "cmc.evm.risk", "cmc.evm.estimate.line", "cmc.evm.baseline.line",
                      "cmc.evm.change.request", "cmc.evm.task.line", "cmc.evm.curve"):
            with self.subTest(model=model):
                self.assertFalse(env_b[model].search([]))
