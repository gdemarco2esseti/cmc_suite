import importlib.util
import os
from datetime import date

from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


def _load_migrate():
    path = os.path.join(get_module_path("cmc_full_costing"), "migrations", "19.0.1.2.0", "post-migrate.py")
    # why: the migrations folder is named after a version ("19.0.1.2.0"), not importable
    spec = importlib.util.spec_from_file_location("cmc_full_costing_post_migrate_1_2_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestCostingResetMigration(TransactionCase):
    """post-migrate 19.0.1.2.0: before AD-001 a reset kept the results, so a
    draft costing run may still show figures that no longer hold."""

    def test_draft_runs_lose_their_stale_results(self):
        period = {"date_from": date(2019, 1, 1), "date_to": date(2019, 3, 31)}
        closing = self.env["cmc.closing.run"].create({**period, "name": "C"})
        product = self.env["product.product"].create({"name": "RM"})
        stale, kept = self.env["cmc.costing.run"].create(
            [{**period, "name": name, "closing_run_id": closing.id} for name in ("stale", "kept")]
        )
        self.env["cmc.costing.result"].create(
            [{"run_id": run.id, "product_id": product.id, "cost_primo": 10.0} for run in (stale, kept)]
        )
        # "kept" is computed; "stale" went through the old reset (draft, results left in place)
        kept.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        self.env.flush_all()
        with self.assertLogs("cmc_full_costing_post_migrate_1_2_0", level="INFO"):
            _load_migrate()(self.env.cr, "19.0.1.1.0")
        self.env.invalidate_all()
        self.assertFalse(stale.result_ids)
        self.assertTrue(kept.result_ids)
