"""WP-06 - the fixture's MRP actuals come from the real producers (cmc_mrp MP/MOD stamping, core scrap, closing).

These checks are GREEN before WP-06: they prove that the numbers the red tests expect are the ones the existing
producers book, so a red process test fails on the missing engine, never on a mis-built fixture.
"""

from odoo.tests.common import tagged

from .common import P_FROM, P_TO, ProcessCase


@tagged("post_install", "-at_install")
class TestProcessFixture(ProcessCase):
    def _cost(self, center, element, product=None):
        domain = [("account_id", "=", center.id), ("cmc_cost_element_id", "=", element.id),
                  ("cmc_excluded", "=", False), ("date", ">=", P_FROM), ("date", "<=", P_TO)]
        if product:
            domain.append(("product_id", "=", product.id))
        return -sum(self.AL.search(domain).mapped("amount"))

    def test_fixture_assembly_actuals(self):
        self.assertAlmostEqual(self._cost(self.d1, self.mp), 52000.0)
        self.assertAlmostEqual(self._cost(self.d1, self.mod), 30000.0)
        self.assertAlmostEqual(self._cost(self.d1, self.overhead), 20000.0)
        self.assertEqual(self.scrap_s.state, "done")

    def test_fixture_finishing_actuals(self):
        self.assertAlmostEqual(self._cost(self.d2, self.mp, self.s), 96800.0)
        self.assertAlmostEqual(self._cost(self.d2, self.mp, self.pk), 4000.0)
        self.assertAlmostEqual(self._cost(self.d2, self.mod), 20000.0)

    def test_fixture_closing_reallocates_maintenance(self):
        line = self.closing.line_ids.filtered(lambda cl: cl.center_id == self.d1)
        self.assertAlmostEqual(line.cost_received, 8800.0)
