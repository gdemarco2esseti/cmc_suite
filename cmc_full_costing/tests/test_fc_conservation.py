from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFullCostingConservation(TransactionCase):
    """Rounding residual, idempotency, indirect figurative spread and the
    guards around the run window / closing state."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        cls.year = date.today().year

    def _center(self, name, amount):
        center = self.AA.create(
            {"name": name, "plan_id": self.plan.id, "cmc_center_type": "final"}
        )
        self.AL.create(
            {"name": "c", "account_id": center.id, "amount": -amount,  # AD-005: cost = negative amount
             "cmc_behavior": "fixed", "date": date(self.year, 6, 15)}
        )
        return center

    def _closing(self):
        closing = self.env["cmc.closing.run"].create(
            {"name": "cl", "method": "direct",
             "date_from": date(self.year, 1, 1), "date_to": date(self.year, 12, 31)}
        )
        closing.action_compute()
        return closing

    def _run(self, closing, lines, products):
        return self.env["cmc.costing.run"].create(
            {
                "name": "cst",
                "closing_run_id": closing.id,
                "date_from": date(self.year, 1, 1),
                "date_to": date(self.year, 12, 31),
                "base": "multiple",
                "product_ids": [(6, 0, products.ids)],
                "line_ids": [(0, 0, ln) for ln in lines],
            }
        )

    def _three_products(self):
        return self.env["product.product"].create(
            [{"name": "R%s" % i, "standard_price": 0} for i in range(3)]
        )

    # a pool that does NOT divide evenly must still be conserved to the cent:
    # that is exactly what largest_remainder exists for
    def test_rounding_residual_is_conserved(self):
        center = self._center("R", 100.0)
        products = self._three_products()
        closing = self._closing()
        run = self._run(
            closing,
            [
                {"center_id": center.id, "product_id": p.id,
                 "driver_id": self.driver.id, "driver_qty": 10}
                for p in products
            ],
            products,
        )
        run.action_compute()
        shares = sorted(run.result_ids.mapped("cost_indiretto"))
        self.assertEqual(sum(shares), 100.00)
        self.assertEqual(shares, [33.33, 33.33, 33.34])

    # computing twice must not duplicate (nor lose) the derived rows
    def test_compute_is_idempotent(self):
        center = self._center("I", 900.0)
        products = self._three_products()
        closing = self._closing()
        run = self._run(
            closing,
            [
                {"center_id": center.id, "product_id": p.id,
                 "driver_id": self.driver.id, "driver_qty": 10}
                for p in products
            ],
            products,
        )
        run.action_compute()
        first = (
            len(run.result_ids),
            len(run.result_ids.quota_ids),
            len(run.result_ids.config_ids),
            sum(run.result_ids.mapped("cost_indiretto")),
        )
        run.action_reset_to_draft()  # AD-001: recompute goes through a reset
        run.action_compute()
        second = (
            len(run.result_ids),
            len(run.result_ids.quota_ids),
            len(run.result_ids.config_ids),
            sum(run.result_ids.mapped("cost_indiretto")),
        )
        self.assertEqual(first, second)

    # TS-002 — an INDIRECT figurative cost is spread over the units consumed
    def test_indirect_figurative_spread(self):
        center = self._center("F", 900.0)
        products = self._three_products()
        element = self.env["cmc.cost.element"].create(
            {"name": "Notional rent", "code": "FIG1", "cmc_kind": "figurative"}
        )
        self.env["cmc.figurative.cost"].create(
            {"name": "Rent", "cost_element_id": element.id, "method": "lump",
             "amount": 300.0, "cmc_directness": "indirect"}
        )
        closing = self._closing()
        run = self._run(
            closing,
            [
                {"center_id": center.id, "product_id": p.id,
                 "driver_id": self.driver.id, "driver_qty": qty}
                for p, qty in zip(products, (30, 20, 10))
            ],
            products,
        )
        run.action_compute()
        by_product = {r.product_id: r.cost_figurativo for r in run.result_ids}
        # units 30/20/10 over 60 -> 150 / 100 / 50, pool conserved
        self.assertEqual(by_product[products[0]], 150.0)
        self.assertEqual(by_product[products[1]], 100.0)
        self.assertEqual(by_product[products[2]], 50.0)
        self.assertEqual(sum(run.result_ids.mapped("cost_figurativo")), 300.0)

    # a closing reset to draft must not yield a "computed" costing
    def test_draft_closing_blocks_compute(self):
        center = self._center("D", 100.0)
        products = self._three_products()[:1]
        closing = self._closing()
        run = self._run(
            closing,
            [{"center_id": center.id, "product_id": products.id,
              "driver_id": self.driver.id, "driver_qty": 10}],
            products,
        )
        closing.action_reset_to_draft()
        with self.assertRaisesRegex(UserError, "not computed"):
            run.action_compute()

    # the costing window must sit inside the closing window: a Q1 closing must
    # not be combined with Q3 period costs
    def test_period_outside_closing_window(self):
        closing = self.env["cmc.closing.run"].create(
            {"name": "q1", "method": "direct",
             "date_from": date(self.year, 1, 1), "date_to": date(self.year, 3, 31)}
        )
        closing.action_compute()
        with self.assertRaises(ValidationError):
            self.env["cmc.costing.run"].create(
                {"name": "q3", "closing_run_id": closing.id, "base": "multiple",
                 "date_from": date(self.year, 7, 1),
                 "date_to": date(self.year, 9, 30)}
            )

    # a lump-sum amount must survive a change of method (the editable compute
    # used to null it for every record it left unset)
    def test_lump_amount_survives_method_toggle(self):
        element = self.env["cmc.cost.element"].create(
            {"name": "Notional pay", "code": "FIG2", "cmc_kind": "figurative"}
        )
        fc = self.env["cmc.figurative.cost"].create(
            {"name": "Pay", "cost_element_id": element.id, "method": "lump",
             "amount": 100.0}
        )
        fc.method = "interest"
        fc.method = "lump"
        self.assertEqual(fc.amount, 100.0)
