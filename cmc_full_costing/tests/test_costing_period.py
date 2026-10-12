from datetime import date

import psycopg2

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestCostingPeriod(TransactionCase):
    """Refactor 2026-10, task 1 — a costing run must sit inside the period of
    the closing run it reads from; an undated closing cannot exist, so the
    check can never be skipped."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Closing = cls.env["cmc.closing.run"]
        cls.Costing = cls.env["cmc.costing.run"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Hours", "code": "H"})
        cls.year = 2019
        cls.center = cls.AA.create({"name": "Assembly", "plan_id": cls.plan.id, "cmc_center_type": "final"})
        cls.AL.create(
            {"name": "ind", "account_id": cls.center.id, "amount": -1000, "date": date(cls.year, 2, 15),
             "cmc_behavior": "fixed", "cmc_directness": "indirect"}
        )
        cls.product = cls.env["product.product"].create({"name": "P1", "standard_price": 10})
        cls.closing_q1 = cls.Closing.create(
            {"name": "Q1", "method": "direct", "date_from": date(cls.year, 1, 1), "date_to": date(cls.year, 3, 31)}
        )
        cls.closing_q1.action_compute()

    def _costing_vals(self, closing, date_from, date_to):
        return {
            "name": "Costing",
            "closing_run_id": closing.id,
            "date_from": date_from,
            "date_to": date_to,
            "base": "multiple",
            "product_ids": [(6, 0, self.product.ids)],
            "line_ids": [
                (0, 0, {"center_id": self.center.id, "product_id": self.product.id,
                        "driver_id": self.driver.id, "driver_qty": 10})
            ],
        }

    # AC1.3: a Q3 costing cannot be combined with a Q1 closing
    def test_ac13_costing_fully_outside_closing_raises(self):
        with self.assertRaises(ValidationError):
            self.Costing.create(self._costing_vals(self.closing_q1, date(self.year, 7, 1), date(self.year, 9, 30)))

    # AC1.3: starting before the closing window is out of period
    def test_ac13_costing_starting_before_closing_raises(self):
        with self.assertRaises(ValidationError):
            self.Costing.create(self._costing_vals(self.closing_q1, date(self.year - 1, 12, 1), date(self.year, 3, 31)))

    # AC1.3: ending after the closing window is out of period
    def test_ac13_costing_ending_after_closing_raises(self):
        with self.assertRaises(ValidationError):
            self.Costing.create(self._costing_vals(self.closing_q1, date(self.year, 1, 1), date(self.year, 4, 1)))

    # AC1.3: moving an existing costing out of the closing window is refused too (write path)
    def test_ac13_moving_costing_out_of_closing_raises(self):
        costing = self.Costing.create(self._costing_vals(self.closing_q1, date(self.year, 1, 1), date(self.year, 3, 31)))
        with self.assertRaises(ValidationError):
            costing.write({"date_from": date(self.year, 7, 1), "date_to": date(self.year, 9, 30)})

    # AC1.3: a costing inside (boundaries included) the closing window is accepted and computes
    def test_ac13_costing_within_closing_ok(self):
        costing = self.Costing.create(self._costing_vals(self.closing_q1, date(self.year, 1, 1), date(self.year, 3, 31)))
        costing.action_compute()
        self.assertEqual(costing.state, "done")
        self.assertAlmostEqual(sum(costing.result_ids.mapped("cost_indiretto")), 1000.0, places=2)

    # AC1.3 / AC1.1: the period check must not be skippable through an undated
    # closing — such a closing must not even be creatable, so a Q3 costing can
    # never silently absorb the whole overhead history
    @mute_logger("odoo.sql_db")
    def test_ac13_no_costing_on_undated_closing(self):
        with self.assertRaises(psycopg2.errors.NotNullViolation), self.env.cr.savepoint():
            closing = self.Closing.create({"name": "Undated", "method": "direct"})
            self.env.flush_all()
            closing.action_compute()
            self.Costing.create(self._costing_vals(closing, date(self.year, 7, 1), date(self.year, 9, 30)))
