from datetime import date

import psycopg2

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAbc(TransactionCase):
    """Fase 3 — Activity-Based Costing: two-stage allocation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Act = cls.env["cmc.abc.activity"]
        cls.Run = cls.env["cmc.abc.run"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create(
            {"name": "Reparto", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.a = cls.Act.create({"name": "Setup", "code": "SET"})
        cls.b = cls.Act.create({"name": "Collaudo", "code": "QC"})
        cls.p1 = cls.env["product.product"].create({"name": "P1"})
        cls.p2 = cls.env["product.product"].create({"name": "P2"})

    @classmethod
    def _pool(cls, activity, amount, **kw):
        vals = {
            "name": "cost", "account_id": cls.center.id,
            "amount": amount, "cmc_activity_id": activity.id,
        }
        vals.update(kw)
        return cls.AL.create(vals)

    def _run(self, lines, **kw):
        vals = {"name": "ABC", "line_ids": [(0, 0, ln) for ln in lines]}
        vals.update(kw)
        run = self.Run.create(vals)
        run.action_compute()
        return run

    def _res(self, run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)

    # single activity: pool 1000, drivers 60/40 -> 600/400
    def test_single_activity(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 600.0)
        self.assertEqual(self._res(run, self.p2).amount, 400.0)
        self.assertEqual(run.state, "done")

    # two activities sum on each product
    def test_multi_activity(self):
        self._pool(self.a, 1000)
        self._pool(self.b, 500)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
            {"activity_id": self.b.id, "product_id": self.p1.id, "driver_qty": 50},
            {"activity_id": self.b.id, "product_id": self.p2.id, "driver_qty": 50},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 850.0)  # 600 + 250
        self.assertEqual(self._res(run, self.p2).amount, 650.0)  # 400 + 250

    # conservation: awkward split re-sums exactly to the pool
    def test_conservation(self):
        self._pool(self.a, 100)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 1},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 2},
        ])
        total = sum(run.result_ids.mapped("amount"))
        self.assertEqual(total, 100.0)

    # pool read as magnitude regardless of sign (costs may be negative)
    def test_negative_amount_pool(self):
        self._pool(self.a, -1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 600.0)

    # empty pool -> zero results, no crash
    def test_empty_pool(self):
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 0.0)

    # recompute is idempotent (no duplicated results)
    def test_recompute_idempotent(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        n1 = len(run.result_ids)
        run.action_compute()
        self.assertEqual(len(run.result_ids), n1)
        self.assertEqual(self._res(run, self.p1).amount, 600.0)

    # driver quantity must be positive
    def test_qty_positive(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self.Run.create({
                    "name": "bad",
                    "line_ids": [(0, 0, {
                        "activity_id": self.a.id, "product_id": self.p1.id,
                        "driver_qty": 0,
                    })],
                })
                self.env.flush_all()

    # --- B-2: activities without a driver line must not vanish ---------------
    def test_unallocated_activity_surfaced(self):
        self._pool(self.a, 1000)
        self._pool(self.b, 400)  # nobody entered a driver row for b
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(run.unallocated_amount, 400.0)
        unalloc = run.activity_result_ids.filtered("unallocated")
        self.assertEqual(unalloc.activity_id, self.b)
        self.assertEqual(unalloc.amount, 400.0)
        # allocated results untouched, and the residual is visible in the reclass
        self.assertEqual(sum(run.result_ids.mapped("amount")), 1000.0)
        labels = [r["label"] for r in run._cmc_reclass()["rows"]]
        self.assertTrue(any(lb.startswith("Unallocated") for lb in labels))

    # a degenerate (non-zero but meaningless) driver base must not be used as a
    # divisor: the pool stays unallocated instead of exploding the coefficient.
    def test_degenerate_driver_qty(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id,
             "driver_qty": 1e-18},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 0.0)
        self.assertEqual(run.unallocated_amount, 1000.0)

    # --- B-3: a computed run is not editable --------------------------------
    def test_done_run_input_is_locked(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        line = run.line_ids[0]
        with self.assertRaises(UserError):
            line.driver_qty = 5
        with self.assertRaises(UserError):
            line.unlink()
        with self.assertRaises(UserError):
            self.env["cmc.abc.line"].create({
                "run_id": run.id, "activity_id": self.b.id,
                "product_id": self.p1.id, "driver_qty": 1,
            })

    def test_reset_to_draft(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
        ])
        run.action_reset_to_draft()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.result_ids)
        self.assertFalse(run.activity_result_ids)
        run.line_ids[0].driver_qty = 30  # editable again
        run.action_compute()
        self.assertEqual(self._res(run, self.p1).amount, 1000.0)

    # --- period scoping -----------------------------------------------------
    def test_period_scoping(self):
        self._pool(self.a, 1000, date=date(2026, 1, 15))
        self._pool(self.a, 500, date=date(2026, 3, 15))
        run = self._run(
            [{"activity_id": self.a.id, "product_id": self.p1.id,
              "driver_qty": 10}],
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
        self.assertEqual(self._res(run, self.p1).amount, 1000.0)
        run.action_reset_to_draft()
        run.date_to = date(2026, 3, 31)
        run.action_compute()
        self.assertEqual(self._res(run, self.p1).amount, 1500.0)

    # --- security: the run must produce the same numbers for a plain CMC user
    def test_group_cmc_user_end_to_end(self):
        user = new_test_user(
            self.env, login="cmc_abc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
        )
        self._pool(self.a, 1000)
        run = self.Run.with_user(user).create({
            "name": "ABC user",
            "line_ids": [
                (0, 0, {"activity_id": self.a.id, "product_id": self.p1.id,
                        "driver_qty": 60}),
                (0, 0, {"activity_id": self.a.id, "product_id": self.p2.id,
                        "driver_qty": 40}),
            ],
        })
        run.action_compute()
        self.assertEqual(run.state, "done")
        # _read_group applies record rules transparently: a blocked analytic ACL
        # would silently return a SMALLER pool instead of raising.
        self.assertEqual(sum(run.result_ids.mapped("amount")), 1000.0)
        self.assertEqual(
            run.result_ids.filtered(lambda r: r.product_id == self.p1).amount,
            600.0,
        )
        self.assertFalse(run.unallocated_amount)

    # --- multi-company ------------------------------------------------------
    def test_two_company_isolation(self):
        c2 = self.env["res.company"].create({"name": "ABC Company B"})
        center_b = self.AA.create({
            "name": "Reparto B", "plan_id": self.plan.id,
            "cmc_center_type": "final", "company_id": c2.id,
        })
        act_b = self.Act.create(
            {"name": "Setup B", "code": "SETB", "company_id": c2.id}
        )
        self._pool(self.a, 1000)
        self.AL.create({
            "name": "cost b", "account_id": center_b.id, "amount": 500,
            "cmc_activity_id": act_b.id, "company_id": c2.id,
        })
        run_a = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(sum(run_a.result_ids.mapped("amount")), 1000.0)
        self.assertFalse(run_a.activity_result_ids.filtered("unallocated"))

        run_b = self.Run.create({
            "name": "ABC B", "company_id": c2.id,
            "line_ids": [(0, 0, {
                "activity_id": act_b.id, "product_id": self.p1.id,
                "driver_qty": 10,
            })],
        })
        run_b.action_compute()
        self.assertEqual(sum(run_b.result_ids.mapped("amount")), 500.0)
