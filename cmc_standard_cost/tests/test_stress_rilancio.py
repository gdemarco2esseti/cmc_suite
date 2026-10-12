"""Stress test 2026-10 (rilancio) - FX-020: roll-up of a product without a manufacturing BoM on cmc_standard_cost.

``cmc.plan.version.action_cmc_std_rollup(products)``: a product without a normal (manufacturing) BoM in the version
company raises UserError naming the product, BEFORE any card is written (no silent "nothing happened").
"""

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import Y_FROM, Y_TO, StdCase


@tagged("post_install", "-at_install")
class TestStdStressRilancio(StdCase):
    def setUp(self):
        super().setUp()
        self.version = self.Version.create(
            {"name": "STD rilancio", "version_type": "standard", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        self.version.action_cmc_std_prefill_rates(self.budget, basis="practical")

    def _cards(self):
        return self.env["cmc.standard.card"].search([("version_id", "=", self.version.id)])

    def test_fx020_rollup_product_without_bom_names_it(self):
        # FX-020: a raw material (no BoM) is refused with its name, not silently skipped
        with self.assertRaises(UserError) as caught:
            self.version.with_user(self.manager).action_cmc_std_rollup(self.a)
        self.assertIn(self.a.display_name, str(caught.exception))
        self.assertFalse(self._cards())

    def test_fx020_rollup_mixed_selection_creates_nothing(self):
        # FX-020: one product without a BoM blocks the whole roll-up; the products with a BoM get no card either
        with self.assertRaises(UserError) as caught:
            self.version.with_user(self.manager).action_cmc_std_rollup(self.f | self.a)
        message = str(caught.exception)
        self.assertIn(self.a.display_name, message)
        self.assertNotIn(self.f.display_name, message)
        self.assertFalse(self._cards())

    def test_fx020_rollup_kit_bom_only_is_refused(self):
        # FX-020: a kit (phantom) BoM is not a manufacturing BoM: no standard card can be built from it
        kit = self._product("STD kit")
        self.env["mrp.bom"].create({
            "product_tmpl_id": kit.product_tmpl_id.id, "product_qty": 1.0, "type": "phantom", "company_id": self.company.id,
            "bom_line_ids": [Command.create({"product_id": self.c.id, "product_qty": 2.0})],
        })
        with self.assertRaises(UserError) as caught:
            self.version.with_user(self.manager).action_cmc_std_rollup(kit)
        self.assertIn(kit.display_name, str(caught.exception))
        self.assertFalse(self._cards())

    def test_fx020_rollup_with_bom_still_builds_cards(self):
        # FX-020 regression: products with a manufacturing BoM still get their cards
        self.version.with_user(self.manager).action_cmc_std_rollup(self.f | self.k)
        self.assertLessEqual(self.f | self.k, self._cards().product_id)
