"""FC-09 / AD-108 - publish standard unit costs to cmc.rate, push to standard_price through the core revaluation,
card-vs-valuation reconciliation. CONTRACT: common.py §1 (approval publishing, push, reconciliation).
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .common import TODAY, StdCase


@tagged("post_install", "-at_install")
class TestStdPublish(StdCase):
    def test_approval_publishes_unit_costs(self):
        # AD-108: consumers read the standard unit cost of a product from the registry, not from the card
        version = self._std_version()
        obj_f = self.env["cmc.cost.object"]._cmc_ensure_objects(self.f, company=self.company)
        Rate = self.env["cmc.rate"]
        self.assertAlmostEqual(Rate._cmc_lookup(obj_f, self.produced, TODAY, origin=version, component="total").value, 32.0)
        self.assertAlmostEqual(Rate._cmc_lookup(obj_f, self.produced, TODAY, origin=version, component="variable").value, 27.0)
        self.assertAlmostEqual(Rate._cmc_lookup(obj_f, self.produced, TODAY, origin=version, component="fixed").value, 5.0)
        totals = Rate.search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", version.id),
                              ("holder_model", "=", "cmc.cost.object"), ("holder_id", "=", obj_f.id), ("component", "=", "total")])
        self.assertEqual(len(totals), 12, "one rate per month of the span")

    def test_draft_version_publishes_nothing(self):
        # AD-108: a draft standard is no planned rate yet
        version = self._std_version(approve=False)
        self.assertFalse(self.env["cmc.rate"].search_count(
            [("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", version.id), ("holder_model", "=", "cmc.cost.object")]
        ))

    def test_push_standard_price_through_core_revaluation(self):
        # FC-09: the manager pushes the card cost to standard_price; core records the revaluation (product.value)
        version = self._std_version()
        version.with_user(self.manager).action_cmc_std_push_prices()
        self.assertAlmostEqual(self.f.with_company(self.company).standard_price, 32.0)
        self.assertAlmostEqual(self.g.with_company(self.company).standard_price, 38.0)
        value = self.env["product.value"].search([("product_id", "=", self.f.id)], order="id desc", limit=1)
        self.assertAlmostEqual(value.value, 32.0)

    def test_push_skips_non_standard_cost_method(self):
        # FC-09: only products valued at standard cost take the standard; a FIFO product keeps its cost
        fifo = self.env["product.category"].create(
            {"name": "STD fifo", "property_cost_method": "fifo", "property_valuation": "periodic"}
        )
        product = self._product("STD fifo product", 3.0, categ=fifo)
        self._bom(product, [(self.c, 1)])
        version = self._std_version(products=self.f | product)
        version.with_user(self.manager).action_cmc_std_push_prices()
        self.assertAlmostEqual(product.with_company(self.company).standard_price, 3.0)
        self.assertAlmostEqual(self.f.with_company(self.company).standard_price, 32.0)

    def test_push_refused_on_draft_version(self):
        # FC-09: only an approved standard reaches the valuation
        version = self._std_version(approve=False)
        with self.assertRaises(UserError):
            version.with_user(self.manager).action_cmc_std_push_prices()

    def test_push_refused_to_user(self):
        # AD-115: pushing to the valuation is a manager action
        version = self._std_version()
        with self.assertRaises(AccessError):
            version.with_user(self.user).action_cmc_std_push_prices()
        self.assertAlmostEqual(self.f.with_company(self.company).standard_price, 0.0)

    def test_reconciliation_card_vs_valuation(self):
        # FC-09: the managerial standard is reconciled with the valuation price, before and after the push
        version = self._std_version()
        before = {row["product"]: row for row in version._cmc_std_reconciliation()}
        row_f = before[self.f]
        self.assertAlmostEqual(row_f["card_unit_cost"], 32.0)
        self.assertAlmostEqual(row_f["valuation_price"], 0.0)
        self.assertAlmostEqual(row_f["difference"], -32.0)
        self.assertAlmostEqual(row_f["qty_available"], 10.0)
        self.assertAlmostEqual(row_f["value_difference"], -320.0)
        version.with_user(self.manager).action_cmc_std_push_prices()
        after = {row["product"]: row for row in version._cmc_std_reconciliation()}
        self.assertAlmostEqual(after[self.f]["difference"], 0.0)
        self.assertAlmostEqual(after[self.g]["difference"], 0.0)
