import importlib.util
import os
from datetime import date

from odoo.modules import get_module_path
from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, tagged

MIGRATION_VERSION = "19.0.1.2.0"
ON_DATE = date(2026, 3, 15)


def _migration_path():
    return os.path.join(get_module_path("cmc_base"), "migrations", MIGRATION_VERSION, "post-migrate.py")


def _load_migrate():
    # why: the migrations folder is named after a version ("19.0.1.2.0"), not importable
    spec = importlib.util.spec_from_file_location("cmc_base_post_migrate_1_2_0", _migration_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestCostSignMigration(TransactionCase):
    """AD-005 post-migrate 19.0.1.2.0: manual CMC cost entries booked with the
    old POSITIVE convention are flipped to negative; document-born lines,
    unclassified lines, already-negative lines and excluded twins stay as they are."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Sign migration center", "plan_id": plan.id, "cmc_center_type": "final"}
        )

    def _line(self, amount, **kw):
        # "old" data: before AD-005 a manual cost could be booked positive through the ORM
        vals = {"name": "old", "account_id": self.center.id, "amount": amount, "date": ON_DATE}
        vals.update(kw)
        return self.AL.create(vals)

    def _migrate(self):
        self.assertTrue(
            os.path.isfile(_migration_path()),
            "cmc_base/migrations/%s/post-migrate.py is missing" % MIGRATION_VERSION,
        )
        _load_migrate()(self.env.cr, "19.0.1.1.0")
        self.env.invalidate_all()

    def test_manifest_version_bumped(self):
        # AD-005: the migration only runs if the installed version moves to 19.0.1.2.0
        self.assertEqual(get_manifest("cmc_base")["version"], MIGRATION_VERSION)

    def test_manual_classified_positive_line_is_flipped(self):
        # AD-005: a manual cost entry classified by behavior (+300) becomes -300
        line = self._line(300.0, cmc_behavior="fixed")
        self._migrate()
        self.assertEqual(line.amount, -300.0)

    def test_manual_line_classified_by_element_is_flipped(self):
        # AD-005: classification by cost element alone is enough
        element = self.env["cmc.cost.element"].create({"name": "Sign mig element", "code": "SMIG"})
        line = self._line(300.0, cmc_cost_element_id=element.id)
        self._migrate()
        self.assertEqual(line.amount, -300.0)

    def test_manual_line_classified_by_activity_is_flipped(self):
        # AD-005: an ABC activity tag counts as CMC classification when cmc_abc is installed
        if "cmc.abc.activity" not in self.env:
            self.skipTest("cmc_abc not installed")
        activity = self.env["cmc.abc.activity"].create({"name": "Sign mig activity", "code": "SMIGA"})
        line = self._line(300.0, cmc_activity_id=activity.id)
        self._migrate()
        self.assertEqual(line.amount, -300.0)

    def test_unclassified_positive_line_untouched(self):
        # AD-005: a positive line nobody classified is not a CMC cost entry (e.g. revenue)
        line = self._line(300.0)
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_document_born_positive_line_untouched(self):
        # AD-005: a line with a financial account comes from a document; its sign is Odoo's, not ours
        if "general_account_id" not in self.AL._fields:
            self.skipTest("account not installed")
        account = self.env["account.account"].create(
            {"name": "Sign mig income", "code": "SMIG01", "account_type": "income"}
        )
        line = self._line(300.0, cmc_behavior="fixed", general_account_id=account.id)
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_non_other_category_positive_line_untouched(self):
        # AD-005: a line categorised by a document flow (vendor bill, MO...) is never flipped
        if "category" not in self.AL._fields:
            self.skipTest("account not installed")
        line = self._line(300.0, cmc_behavior="fixed", category="vendor_bill")
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_already_negative_line_untouched(self):
        # AD-005: a cost already in the natural sign is not flipped back
        line = self._line(-300.0, cmc_behavior="fixed")
        self._migrate()
        self.assertEqual(line.amount, -300.0)

    def test_excluded_line_untouched(self):
        # AD-005: an excluded twin is outside the cost base, the migration leaves it alone
        # why sudo: the exclusion flag is system-only by design
        line = self.AL.sudo().create(
            {"name": "twin", "account_id": self.center.id, "amount": 300.0, "date": ON_DATE,
             "cmc_behavior": "fixed", "cmc_excluded": True}
        )
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_stock_linked_positive_line_untouched(self):
        # AD-005: stock_account valuation lines are category 'other' without account/document:
        # only the stock.move M2M identifies them
        if "stock.move" not in self.env:
            self.skipTest("stock_account not installed")
        line = self._line(300.0, cmc_behavior="fixed")
        product = self.env["product.product"].create({"name": "Sign mig product"})
        move = self.env["stock.move"].create(
            {
                "product_id": product.id,
                "product_uom_qty": 1,
                "location_id": self.env.ref("stock.stock_location_stock").id,
                "location_dest_id": self.env.ref("stock.stock_location_customers").id,
                "analytic_account_line_ids": [(6, 0, line.ids)],
            }
        )
        self.assertTrue(move)
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_rule_classified_positive_line_untouched(self):
        # AD-005: an entry classified by a cmc_account rule (not manual) is not ours to flip
        if "cmc_classified_manually" not in self.AL._fields:
            self.skipTest("cmc_account not installed")
        line = self._line(300.0, cmc_behavior="fixed")
        # why raw SQL: ORM writes on the flag would trigger the reset-to-rules logic
        self.env.flush_all()
        self.env.invalidate_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_classified_manually = FALSE WHERE id = %s", (line.id,))
        self._migrate()
        self.assertEqual(line.amount, 300.0)

    def test_ambiguous_negative_line_is_warned_not_changed(self):
        # AD-005: a classified document-less negative line may be an old reversal: warn, never touch
        line = self._line(-300.0, cmc_behavior="fixed")
        with self.assertLogs("cmc_base_post_migrate_1_2_0", level="WARNING") as logs:
            self._migrate()
        self.assertIn("verify by hand", logs.output[0])
        self.assertIn(str(line.id), logs.output[0])
        self.assertEqual(line.amount, -300.0)

    def test_all_capacity_analyses_recomputed(self):
        # AD-005: the raw UPDATE touches amounts: EVERY stored capacity figure is refreshed, not only the flipped lines' rows
        self._line(300.0, cmc_behavior="fixed")
        other = self.env["account.analytic.account"].create(
            {"name": "Sign mig other", "plan_id": self.center.plan_id.id, "cmc_center_type": "final"}
        )
        analysis = self.env["cmc.capacity.analysis"].create(
            {"center_id": other.id, "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "capacity_theoretical": 100, "capacity_practical": 80}
        )
        self.env.cr.execute("UPDATE cmc_capacity_analysis SET fixed_cost = 999 WHERE id = %s", (analysis.id,))
        self._migrate()
        self.assertEqual(analysis.fixed_cost, 0.0)
