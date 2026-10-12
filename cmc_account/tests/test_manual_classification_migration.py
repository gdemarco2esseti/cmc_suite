import importlib.util
import os

from odoo import Command, fields
from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, tagged

MIGRATION_VERSION = "19.0.1.2.0"
LOGGER = "cmc_account_post_migrate_1_2_0"
OLD_COLUMNS = ("cmc_classified_manually", "cmc_behavior", "cmc_directness", "cmc_cost_element_id")


def _load_migrate():
    path = os.path.join(get_module_path("cmc_account"), "migrations", MIGRATION_VERSION, "post-migrate.py")
    # why: the migrations folder is named after a version ("19.0.1.2.0"), not importable
    spec = importlib.util.spec_from_file_location(LOGGER, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestManualClassificationMigration(TransactionCase):
    """RN-001: the override of a DRAFT entry lives only on the journal item
    columns (button_draft deletes its analytic lines): the migration must carry
    it to cmc.manual.classification before dropping them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        AA = cls.env["account.analytic.account"]
        cls.center_x = AA.create({"name": "Mig X", "plan_id": plan.id, "cmc_center_type": "final"})
        cls.center_y = AA.create({"name": "Mig Y", "plan_id": plan.id, "cmc_center_type": "final"})
        cls.expense = cls.env["account.account"].create({"name": "Mig exp", "code": "601977", "account_type": "expense"})
        cls.element = cls.env["cmc.cost.element"].create({"name": "Mig element", "code": "MIGEL"})
        cls.partner = cls.env["res.partner"].create({"name": "Mig supplier"})
        # the old columns, as a 19.0.1.1.0 database still has them
        cls.env.cr.execute(
            "ALTER TABLE account_move_line "
            "ADD COLUMN IF NOT EXISTS cmc_classified_manually boolean, "
            "ADD COLUMN IF NOT EXISTS cmc_behavior varchar, "
            "ADD COLUMN IF NOT EXISTS cmc_directness varchar, "
            "ADD COLUMN IF NOT EXISTS cmc_cost_element_id integer"
        )

    def _bill(self, distribution, post):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.context_today(self.env.user),
            "date": fields.Date.context_today(self.env.user),
            "invoice_line_ids": [Command.create({
                "name": "migrated",
                "quantity": 1.0,
                "price_unit": 100.0,
                "account_id": self.expense.id,
                "tax_ids": [Command.clear()],
                "analytic_distribution": distribution,
            })],
        })
        if post:
            bill.action_post()
        return bill

    def _old_override(self, move_line):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE account_move_line SET cmc_classified_manually = TRUE, cmc_behavior = 'fixed', "
            "cmc_directness = 'direct', cmc_cost_element_id = %s WHERE id = %s",
            (self.element.id, move_line.id),
        )

    def _migrate(self):
        self.env.invalidate_all()
        _load_migrate()(self.env.cr, "19.0.1.1.0")
        self.env.invalidate_all()

    def _records(self, move_line):
        return self.env["cmc.manual.classification"].search([("move_line_id", "=", move_line.id)])

    def _assert_values(self, record):
        self.assertEqual(record.cmc_behavior, "fixed")
        self.assertEqual(record.cmc_directness, "direct")
        self.assertEqual(record.cmc_cost_element_id, self.element)

    def test_draft_override_is_carried_and_applied_on_repost(self):
        posted = self._bill({str(self.center_x.id): 60, str(self.center_y.id): 40}, post=True)
        posted_line = posted.invoice_line_ids
        line_x = posted_line.analytic_line_ids.filtered(lambda a: a.account_id == self.center_x)
        line_x.write({"cmc_behavior": "fixed", "cmc_directness": "direct", "cmc_cost_element_id": self.element.id})
        # the old world kept the override on the analytic line only, never in the new model
        self._records(posted_line).unlink()
        draft = self._bill({str(self.center_x.id): 100}, post=False)
        draft_line = draft.invoice_line_ids
        self._old_override(draft_line)

        self._migrate()

        posted_records = self._records(posted_line)
        self.assertEqual(posted_records.center_id, self.center_x)
        self._assert_values(posted_records)
        draft_records = self._records(draft_line)
        self.assertEqual(draft_records.center_id, self.center_x)
        self._assert_values(draft_records)
        self.env.cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'account_move_line' AND column_name IN %s", (OLD_COLUMNS,)
        )
        self.assertFalse(self.env.cr.fetchall(), "the old columns are dropped")

        draft.action_post()
        new_x = draft_line.analytic_line_ids.filtered(lambda a: a.account_id == self.center_x)
        self.assertTrue(new_x.cmc_classified_manually)
        self.assertEqual(new_x.cmc_behavior, "fixed")
        self.assertEqual(new_x.cmc_cost_element_id, self.element)

    def test_multi_plan_key_and_no_distribution(self):
        # key "id1,id2": every center of the key; a manual line with no distribution is warned, not guessed
        draft = self._bill({"%s,%s" % (self.center_x.id, self.center_y.id): 100}, post=False)
        orphan = self._bill(False, post=False)
        self._old_override(draft.invoice_line_ids)
        self._old_override(orphan.invoice_line_ids)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self._migrate()
        self.assertEqual(self._records(draft.invoice_line_ids).center_id, self.center_x | self.center_y)
        self.assertFalse(self._records(orphan.invoice_line_ids))
        self.assertTrue(any("WARNING" in out and str(orphan.invoice_line_ids.id) in out for out in logs.output))
        self.assertTrue(any("INFO" in out for out in logs.output))
