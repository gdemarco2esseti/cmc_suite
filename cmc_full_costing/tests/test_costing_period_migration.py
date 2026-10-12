import importlib.util
import os
from datetime import date

from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, tagged


def _load_migrate():
    path = os.path.join(get_module_path("cmc_full_costing"), "migrations", "19.0.1.1.0", "pre-migrate.py")
    # why: the migrations folder is named after a version ("19.0.1.1.0"), not importable
    spec = importlib.util.spec_from_file_location("cmc_full_costing_pre_migrate_1_1_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestCostingPeriodMigration(TransactionCase):
    """pre-migrate 19.0.1.1.0: a costing run saved before its period became
    mandatory takes the period of the closing run it reads from."""

    def test_undated_costing_takes_the_closing_period(self):
        closing = self.env["cmc.closing.run"].create(
            {"name": "C", "method": "direct", "date_from": date(2019, 1, 1), "date_to": date(2019, 3, 31)}
        )
        dated = self.env["cmc.costing.run"].create(
            {"name": "dated", "closing_run_id": closing.id, "date_from": date(2019, 2, 1), "date_to": date(2019, 2, 28)}
        )
        undated = self.env["cmc.costing.run"].create(
            {"name": "undated", "closing_run_id": closing.id, "date_from": date(2019, 1, 1), "date_to": date(2019, 1, 2)}
        )
        self.env.flush_all()
        self.env.cr.execute("ALTER TABLE cmc_costing_run ALTER COLUMN date_from DROP NOT NULL, ALTER COLUMN date_to DROP NOT NULL")
        self.env.cr.execute("UPDATE cmc_costing_run SET date_from = NULL, date_to = NULL WHERE id = %s", (undated.id,))
        self.env.invalidate_all()

        _load_migrate()(self.env.cr, "19.0.1.0.0")
        self.env.invalidate_all()

        self.assertEqual((undated.date_from, undated.date_to), (date(2019, 1, 1), date(2019, 3, 31)))
        self.assertEqual((dated.date_from, dated.date_to), (date(2019, 2, 1), date(2019, 2, 28)))
