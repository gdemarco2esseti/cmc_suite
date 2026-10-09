from datetime import date

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestCapacityMultiLine(TransactionCase):
    """AC6 — the capacity-sync hook on account.analytic.line must work on
    multi-record create/write/unlink (e.g. a vendor bill split over two
    centers), not only one line at a time."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.CA = cls.env["cmc.capacity.analysis"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center_x = cls.AA.create(
            {"name": "Centro X", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.center_y = cls.AA.create(
            {"name": "Centro Y", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.d_from = date(2026, 1, 1)
        cls.d_to = date(2026, 1, 31)
        cls.d_line = date(2026, 1, 15)
        cls.ca_x = cls._analysis(cls.center_x)
        cls.ca_y = cls._analysis(cls.center_y)
        # booker of analytic lines without CMC rights (same profile as test_security)
        cls.outsider = new_test_user(
            cls.env,
            login="cmc_ml_outsider",
            groups="base.group_user,analytic.group_analytic_accounting",
        )
        # plain internal CMC user (group_cmc_user implies analytic accounting)
        cls.cmc_user = new_test_user(
            cls.env, login="cmc_ml_user", groups="base.group_user,cmc_base.group_cmc_user"
        )

    @classmethod
    def _analysis(cls, center):
        return cls.CA.create(
            {
                "center_id": center.id,
                "date_from": cls.d_from,
                "date_to": cls.d_to,
                "capacity_practical": 100.0,
                "capacity_actual": 80.0,
            }
        )

    def _vals(self, center, amount, behavior="fixed"):
        return {
            "name": "split cost",
            "account_id": center.id,
            "amount": amount,
            "date": self.d_line,
            "cmc_behavior": behavior,
        }

    def _create_pair(self, env_user=None):
        AL = self.AL.with_user(env_user) if env_user else self.AL
        return AL.create(
            [self._vals(self.center_x, 1000.0), self._vals(self.center_y, 500.0)]
        )

    def _assert_capacity(self, fixed_x, fixed_y):
        self.assertRecordValues(
            self.ca_x | self.ca_y,
            [
                {"fixed_cost": fixed_x, "imputed_fixed_cost": fixed_x * 0.8,
                 "unused_capacity_cost": fixed_x * 0.2},
                {"fixed_cost": fixed_y, "imputed_fixed_cost": fixed_y * 0.8,
                 "unused_capacity_cost": fixed_y * 0.2},
            ],
        )

    # ---- AC6.1 — create multi-riga -------------------------------------------

    def test_ac61_create_multi_fixed_lines_two_centers(self):
        # AC6.1: one create([...]) with fixed lines on two centers refreshes both capacities
        self._assert_capacity(0.0, 0.0)
        self._create_pair()
        self._assert_capacity(1000.0, 500.0)

    def test_ac61_create_multi_fixed_lines_same_center(self):
        # AC6.1 edge: two fixed lines on the SAME center in one create sum up
        self.AL.create(
            [self._vals(self.center_x, 300.0), self._vals(self.center_x, 200.0)]
        )
        self._assert_capacity(500.0, 0.0)

    def test_ac61_create_multi_mixed_behavior(self):
        # AC6.1 edge: variable lines in the same batch must not feed the fixed pool
        self.AL.create(
            [
                self._vals(self.center_x, 1000.0),
                self._vals(self.center_y, 500.0),
                self._vals(self.center_x, 999.0, behavior="variable"),
            ]
        )
        self._assert_capacity(1000.0, 500.0)

    # ---- AC6.2 — write / unlink multi-riga -----------------------------------

    def test_ac62_write_multi_amount(self):
        # AC6.2: a single write changing amount on both lines refreshes both capacities
        lines = self._create_pair()
        lines.write({"amount": 2000.0})
        self._assert_capacity(2000.0, 2000.0)

    def test_ac62_write_multi_behavior_to_variable(self):
        # AC6.2: reclassifying fixed->variable in one write empties both fixed pools
        lines = self._create_pair()
        lines.write({"cmc_behavior": "variable"})
        self._assert_capacity(0.0, 0.0)

    def test_ac62_write_multi_date_out_of_period(self):
        # AC6.2: moving both lines out of the period (old rows) must refresh the capacities left behind
        lines = self._create_pair()
        lines.write({"date": date(2026, 2, 15)})
        self._assert_capacity(0.0, 0.0)

    def test_ac62_unlink_multi(self):
        # AC6.2: unlinking both lines at once removes their cost from both capacities
        lines = self._create_pair()
        lines.unlink()
        self._assert_capacity(0.0, 0.0)

    # ---- AC6.3 — eseguito con utenti non amministratori ----------------------

    def test_ac63_outsider_multi_crud_refreshes_capacity(self):
        # AC6.3: a non-CMC booker (bills/timesheets) does multi-line CRUD; sudo hook keeps capacities in sync
        lines = self._create_pair(env_user=self.outsider)
        self._assert_capacity(1000.0, 500.0)
        lines.with_user(self.outsider).write({"amount": 50.0})
        self._assert_capacity(50.0, 50.0)
        lines.with_user(self.outsider).unlink()
        self._assert_capacity(0.0, 0.0)

    def test_ac63_cmc_user_multi_crud_refreshes_capacity(self):
        # AC6.3: a plain CMC user (no manager, no admin) does the same multi-line CRUD
        lines = self._create_pair(env_user=self.cmc_user)
        self._assert_capacity(1000.0, 500.0)
        lines.with_user(self.cmc_user).write({"amount": 70.0})
        self._assert_capacity(70.0, 70.0)
        lines.with_user(self.cmc_user).unlink()
        self._assert_capacity(0.0, 0.0)

    # ---- costo d'accesso — il hook gira su una tabella che cresce con l'uso ----

    def _count_queries(self, func):
        self.env.flush_all()
        count0 = self.cr.sql_log_count
        func()
        self.env.flush_all()
        return self.cr.sql_log_count - count0

    def test_hook_query_cost_independent_of_batch_size(self):
        # Volume: 40 fixed lines on 10 centers in one create; the capacity hook
        # must add O(1) queries over the core cost of the same batch (no per-line
        # search / per-center compute). Baseline = same batch with variable lines,
        # which the hook filters out.
        centers = self.AA.create(
            [
                {"name": f"Vol {i}", "plan_id": self.plan.id, "cmc_center_type": "final"}
                for i in range(10)
            ]
        )
        cas = self.CA.create(
            [
                {
                    "center_id": c.id,
                    "date_from": self.d_from,
                    "date_to": self.d_to,
                    "capacity_practical": 100.0,
                    "capacity_actual": 50.0,
                }
                for c in centers
            ]
        )
        n_lines = 40

        def batch(behavior):
            return [
                self._vals(centers[i % 10], 10.0, behavior=behavior)
                for i in range(n_lines)
            ]

        baseline = self._count_queries(lambda: self.AL.create(batch("variable")))
        with_hook = self._count_queries(
            lambda: (self.AL.create(batch("fixed")), cas.mapped("fixed_cost"))
        )
        # slack: candidate search + one grouped cost read + batched flush of the
        # stored derived fields; independent of n_lines and of the number of centers
        self.assertLessEqual(
            with_hook - baseline,
            20,
            f"capacity hook costs {with_hook - baseline} extra queries for "
            f"{n_lines} lines on 10 centers (baseline {baseline})",
        )
        self.assertEqual(cas.mapped("fixed_cost"), [40.0] * 10)
