"""WP-08 / ABM-01 / RN-003 - the department of an activity cost is read from the CENTERS' plan column.

CONTRACT (see abm_common)
- The center of a ``tag`` / ``trace`` flow (``cmc.abc.flow.center_id``) is the CMC cost center of the ledger
  line (the account with ``cmc_center_type`` / ``cmc_is_period`` on whatever plan column holds it, cf.
  ``account.analytic.line.cmc_center_id``), NOT ``account_id``: in a two-plan company ``account_id`` is a
  project and the department lives on a second plan. ``resource`` flows keep ``resource.center_id``.
- ``run._cmc_activity_center_matrix()`` therefore has ONLY center columns: per center, the column = what that
  center fed into the model (resource pools + tags); Σ = ``total_activity_cost``.
Two-plan fixture (company D, March 2027): plan "WP08 Departments" (root, own column) with centers ZD and SD;
project PRJ on the default plan (``account_id``, no center type) on EVERY line.
  Lines: ZD SAL fixed 1000, SD SAL fixed 600, ZD SAL fixed 200 tagged ACT_A, ZD MP variable 50 direct -> P.
  Resources: RZ ZD % ACT_A 100; RS SD % ACT_A 50 / ACT_B 50.
  Stage 1: ACT_A = 1000 + 300 + tag 200 = 1500 (ZD 1200, SD 300); ACT_B = 300 (SD) -> 1800.
  Columns: ZD 1200, SD 600; PRJ never appears. Trace 50 -> P from ZD.
"""

from odoo.tests.common import tagged

from .abc2_common import D_FROM, D_MID, D_TO, Abc2Case


@tagged("post_install", "-at_install")
class TestAbmTwoPlans(Abc2Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_d = cls.env["res.company"].create({"name": "CMC WP08 two plans"})
        env = cls.env_d = cls._company_env(cls.company_d)
        cls.dept_plan = cls.env["account.analytic.plan"].create({"name": "WP08 Departments"})
        cls.dept_col = cls.dept_plan._column_name()
        Account = env["account.analytic.account"]
        cls.zd = Account.create({"name": "WP08 ZD production", "plan_id": cls.dept_plan.id, "cmc_center_type": "final",
                                 "company_id": cls.company_d.id})
        cls.sd = Account.create({"name": "WP08 SD admin", "plan_id": cls.dept_plan.id, "cmc_center_type": "final",
                                 "company_id": cls.company_d.id})
        cls.prj = Account.create({"name": "WP08 PRJ project", "plan_id": cls.plan.id, "company_id": cls.company_d.id})
        cls.sal_d = env["cmc.cost.element"].create({"name": "WP08 D sal", "code": "WP08DSAL", "company_id": cls.company_d.id})
        cls.drv_a = cls._driver(env, "WP08DA", "transaction", company=cls.company_d)
        cls.drv_b = cls._driver(env, "WP08DB", "transaction", company=cls.company_d)
        cls.act_a = cls._activity("D activity A", "WP08DACTA", False, False, cls.drv_a, env=env)
        cls.act_b = cls._activity("D activity B", "WP08DACTB", False, False, cls.drv_b, env=env)
        prod = cls.env["product.product"].create({"name": "WP08 D product"})
        cls.obj_d = env["cmc.cost.object"]._cmc_ensure_objects(prod, company=cls.company_d)
        cls._dept_cost(cls.zd, 1000, cls.sal_d, "fixed")
        cls._dept_cost(cls.sd, 600, cls.sal_d, "fixed")
        cls._dept_cost(cls.zd, 200, cls.sal_d, "fixed", cmc_activity_id=cls.act_a.id)
        cls._dept_cost(cls.zd, 50, cls.mp, "variable", cmc_cost_object_id=cls.obj_d.id, cmc_directness="direct")
        Res = env["cmc.abc.resource"]
        cls.rz = Res.create({"name": "ZD people", "code": "WP08RZ", "center_id": cls.zd.id, "driver_basis": "percent"})
        cls.rs = Res.create({"name": "SD people", "code": "WP08RS", "center_id": cls.sd.id, "driver_basis": "percent"})

    @classmethod
    def _dept_cost(cls, center, cost, element, behavior, **extra):
        # the department on its own plan column, a project on account_id (the two-plan company)
        return cls.env_d["account.analytic.line"].create({
            "name": "wp08 two plans", "account_id": cls.prj.id, cls.dept_col: center.id, "amount": -cost,
            "company_id": cls.company_d.id, "cmc_cost_element_id": element.id, "cmc_behavior": behavior,
            "date": D_MID, **extra,
        })

    def _run(self):
        run = self.env_d["cmc.abc.run"].create({
            "name": "WP08 two plans", "company_id": self.company_d.id, "date_from": D_FROM, "date_to": D_TO,
            "trace_direct": True,
            "resource_line_ids": [(0, 0, v) for v in (
                {"resource_id": self.rz.id, "activity_id": self.act_a.id, "percent": 100},
                {"resource_id": self.rs.id, "activity_id": self.act_a.id, "percent": 50},
                {"resource_id": self.rs.id, "activity_id": self.act_b.id, "percent": 50},
            )],
            "line_ids": [(0, 0, v) for v in (
                {"activity_id": self.act_a.id, "cost_object_id": self.obj_d.id, "driver_id": self.drv_a.id, "driver_qty": 1},
                {"activity_id": self.act_b.id, "cost_object_id": self.obj_d.id, "driver_id": self.drv_b.id, "driver_qty": 1},
            )],
        })
        run.action_compute()
        return run

    def test_rn003_stage1_reads_the_department_plan(self):
        # RN-003: precondition - the resource pools already read the centers' column (1500 + 300 = 1800)
        run = self._run()
        self.assertAlmostEqual(self._act(run, self.act_a).resource_cost, 1500.0, places=2)
        self.assertAlmostEqual(self._act(run, self.act_b).resource_cost, 300.0, places=2)
        self.assertAlmostEqual(run.total_activity_cost, 1800.0, places=2)

    def test_rn003_tag_flow_center_is_the_department(self):
        # RN-003: a tag line booked on project PRJ / department ZD is a cost of ZD, not of the project
        tag = self._run().flow_ids.filtered(lambda f: f.stage == "tag")
        self.assertAlmostEqual(tag.amount, 200.0, places=2)
        self.assertEqual(tag.center_id, self.zd)

    def test_rn003_trace_flow_center_is_the_department(self):
        # RN-003: the traced direct cost comes from department ZD as well
        trace = self._run().flow_ids.filtered(lambda f: f.stage == "trace")
        self.assertAlmostEqual(trace.amount, 50.0, places=2)
        self.assertEqual(trace.center_id, self.zd)

    def test_rn003_no_flow_carries_a_non_center_account(self):
        # RN-003: the project of account_id never becomes a department column
        flows = self._run().flow_ids.filtered(lambda f: f.stage in ("resource", "tag", "trace"))
        self.assertEqual(flows.center_id, self.zd | self.sd)

    def test_rn003_matrix_columns_are_the_departments(self):
        # RN-003 / ABM-01: activity x department matrix of a two-plan company
        matrix = self._run()._cmc_activity_center_matrix()
        expected = {(self.act_a.id, self.zd.id): 1200.0, (self.act_a.id, self.sd.id): 300.0, (self.act_b.id, self.sd.id): 300.0}
        self.assertEqual(set(matrix), set(expected))
        for key, amount in expected.items():
            self.assertAlmostEqual(matrix[key], amount, places=2, msg=key)

    def test_rn003_matrix_columns_add_up_per_center(self):
        # RN-003 / ABM-01: column = what the center fed in (ZD 1000 + tag 200, SD 600); Σ = total activity cost
        run = self._run()
        matrix = run._cmc_activity_center_matrix()
        column = {center: sum(v for (_a, c), v in matrix.items() if c == center.id) for center in (self.zd, self.sd)}
        self.assertAlmostEqual(column[self.zd], 1200.0, places=2)
        self.assertAlmostEqual(column[self.sd], 600.0, places=2)
        self.assertAlmostEqual(sum(matrix.values()), run.total_activity_cost, places=2)
