"""STD-01 - standard cost card per product on a standard version: roll-up from BoM/routing, types, versions.

CONTRACT: common.py §1, §2, §4 (hand-computed cards of F, G, K in the fixture docstring).
"""

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import Y_FROM, Y_TO, StdCase


@tagged("post_install", "-at_install")
class TestStdCard(StdCase):
    def test_card_single_level_lines(self):
        # STD-01: card = material qty x planned price + MOD hours x center rate + overhead hours x predetermined rates
        card = self._card(self._std_version(approve=False), self.f)
        self.assertEqual(card.bom_id, self.bom_f)
        lines = card.line_ids.sorted(lambda line: (line.line_type, line.product_id.name or "", line.behavior or ""))
        expected = [
            {"line_type": "labour", "center_id": self.z.id, "quantity": 0.5, "price": 20.0, "amount": 10.0},
            {"line_type": "material", "product_id": self.a.id, "quantity": 2.0, "price": 4.0, "amount": 8.0, "price_source": "planned"},
            {"line_type": "material", "product_id": self.b.id, "quantity": 3.0, "price": 2.0, "amount": 6.0, "price_source": "planned"},
            {"line_type": "overhead", "center_id": self.z.id, "behavior": "fixed", "quantity": 0.5, "price": 10.0, "amount": 5.0},
            {"line_type": "overhead", "center_id": self.z.id, "behavior": "variable", "quantity": 0.5, "price": 6.0, "amount": 3.0},
        ]
        # why per line: assertRecordValues requires the same keys in every expected dict
        self.assertEqual(len(lines), len(expected))
        for line, values in zip(lines, expected, strict=True):
            self.assertRecordValues(line, [values])

    def test_card_single_level_totals(self):
        # STD-01: unit figures by element and by behavior (variable / fixed burden)
        card = self._card(self._std_version(approve=False), self.f)
        self.assertRecordValues(card, [{
            "unit_material": 14.0, "unit_labour": 10.0, "unit_overhead": 8.0, "unit_overhead_fixed": 5.0,
            "unit_cost": 32.0, "unit_fixed": 5.0, "unit_variable": 27.0,
        }])

    def test_card_multi_level_rollup(self):
        # STD-01: a sub-assembly is costed at its own card and keeps its element split in the parent card
        version = self._std_version(approve=False, products=self.g)
        card_f, card_g = self._card(version, self.f), self._card(version, self.g)
        self.assertTrue(card_f, "rolling up G also builds the card of its sub-assembly F")
        line_f = card_g.line_ids.filtered(lambda line: line.product_id == self.f)
        self.assertRecordValues(line_f, [{"line_type": "material", "quantity": 1.0, "price": 32.0, "amount": 32.0,
                                          "price_source": "card", "child_card_id": card_f.id}])
        self.assertRecordValues(card_g, [{
            "unit_material": 17.0, "unit_labour": 13.0, "unit_overhead": 8.0, "unit_overhead_fixed": 5.0,
            "unit_cost": 38.0, "unit_fixed": 5.0, "unit_variable": 33.0,
        }])

    def test_card_price_fallback_to_product_standard_price(self):
        # STD-01: a component without planned price is costed at its standard_price, and says so
        line_c = self._card(self._std_version(approve=False), self.g).line_ids.filtered(lambda line: line.product_id == self.c)
        self.assertRecordValues(line_c, [{"quantity": 2.0, "price": 1.5, "amount": 3.0, "price_source": "product"}])

    def test_card_per_unit_of_bom_quantity(self):
        # STD-01: a BoM for 2 units gives per-unit quantities and hours (4 C / 2, 12 min / 2)
        card = self._card(self._std_version(approve=False), self.k)
        self.assertAlmostEqual(card.line_ids.filtered(lambda line: line.line_type == "material").quantity, 2.0)
        self.assertAlmostEqual(card.line_ids.filtered(lambda line: line.line_type == "labour").quantity, 0.1)
        self.assertAlmostEqual(card.unit_cost, 6.0)

    def test_card_matches_core_bom_price(self):
        # AD-103: same BoM explosion as core "compute price from BoM" when prices and rates coincide
        card = self._card(self._std_version(approve=False), self.k)
        self.assertAlmostEqual(card.unit_cost, self.k._compute_bom_price(self.bom_k), places=4)

    def test_card_center_without_labour_rate_fails_loud(self):
        # STD-01: an operation on a center with no labour rate gives no silent zero labour
        orphan = self._center("STD no rate")
        wc = self._workcenter("STD WC orphan", orphan, 10.0)
        product = self._product("STD orphan product")
        self._bom(product, [(self.c, 1)], [(wc, 60.0)])
        version = self._std_version(approve=False, products=self.env["product.product"])
        with self.assertRaisesRegex(UserError, "STD no rate"):
            version.action_cmc_std_rollup(product)

    def test_rollup_again_replaces_cards(self):
        # STD-01: rolling up twice keeps one card per product, with the new figures
        version = self._std_version(approve=False)
        self.env["cmc.standard.price"].search([("version_id", "=", version.id), ("product_id", "=", self.a.id)]).price = 5.0
        version.action_cmc_std_rollup(self.f)
        cards = version.card_ids.filtered(lambda card: card.product_id == self.f)
        self.assertEqual(len(cards), 1)
        self.assertAlmostEqual(cards.unit_material, 16.0)

    def test_card_unique_per_version_and_product(self):
        # STD-01: one card per product in a version
        version = self._std_version(approve=False)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.env["cmc.standard.card"].create({"version_id": version.id, "product_id": self.f.id})
            self.env.flush_all()

    def test_card_refused_on_budget_version(self):
        # AD-104: cards live on standard versions only
        with self.assertRaises(ValidationError):
            self.env["cmc.standard.card"].create({"version_id": self.budget.id, "product_id": self.f.id})

    def test_standard_type_default_and_keys(self):
        # STD-01: standard types basic / ideal / attainable, attainable by default, carried by the card
        version = self._std_version(approve=False)
        self.assertEqual(version.standard_type, "attainable")
        self.assertEqual(
            {key for key, _label in self.Version._fields["standard_type"].selection}, {"basic", "ideal", "attainable"}
        )
        version.standard_type = "ideal"
        self.assertEqual(self._card(version, self.f).standard_type, "ideal")

    def test_approved_version_locks_cards(self):
        # AD-104: an approved standard version is locked: cards, lines and roll-up
        version = self._std_version()
        card = self._card(version, self.f)
        with self.assertRaises(UserError):
            card.line_ids[:1].price = 1.0
        with self.assertRaises(UserError):
            card.unlink()
        with self.assertRaises(UserError):
            version.action_cmc_std_rollup(self.f)

    def test_copy_gives_updated_draft_version(self):
        # STD-01 versions: the copy of an approved standard is an editable draft with the same cards (kaizen update)
        version = self._std_version()
        new = version.copy({"name": "STD updated"})
        self.assertEqual(new.state, "draft")
        self.assertEqual(new.parent_id, version)
        self.assertAlmostEqual(self._card(new, self.f).unit_cost, 32.0)
        self.assertEqual(len(new.std_rate_ids), len(version.std_rate_ids))
        self.assertEqual(len(new.std_price_ids), 2)
        self._card(new, self.f).line_ids[:1].price = 1.0  # editable: no UserError
        self.assertAlmostEqual(self._card(version, self.f).unit_cost, 32.0, msg="the approved original is untouched")

    def test_rollup_on_other_company_bom_ignored(self):
        # AD-115: the BoM of another company is not exploded in this company's version
        company_b = self.env["res.company"].create({"name": "STD company B BoM"})
        product = self._product("STD multi-company")
        self._bom(product, [(self.c, 9)], company=company_b)
        self._bom(product, [(self.c, 1)])
        version = self.Version.create(
            {"name": "STD mc", "version_type": "standard", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        version.action_cmc_std_prefill_rates(self.budget)
        version.action_cmc_std_rollup(product)
        self.assertAlmostEqual(self._card(version, product).unit_material, 1.5)
