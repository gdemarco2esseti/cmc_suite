import importlib.util
import os
from datetime import date

from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


def _load_migrate():
    # why: the migrations folder is named after a version ("19.0.1.1.0"), not importable
    path = os.path.join(get_module_path("cmc_base"), "migrations", "19.0.1.1.0", "pre-migrate.py")
    spec = importlib.util.spec_from_file_location("cmc_base_pre_migrate_1_1_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestClosingMigration(TransactionCase):
    """pre-migrate 19.0.1.1.0: undated closing runs get the span they aggregated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cr = cls.env.cr
        cr.execute("ALTER TABLE cmc_closing_run ALTER COLUMN date_from DROP NOT NULL")
        cr.execute("ALTER TABLE cmc_closing_run ALTER COLUMN date_to DROP NOT NULL")
        # company-less lines can't be created through the ORM nowadays; legacy data may have them
        cr.execute("ALTER TABLE account_analytic_line ALTER COLUMN company_id DROP NOT NULL")
        cls.comp_a, cls.comp_b, cls.comp_d = (cls.env["res.company"].create({"name": "Mig %s" % n}) for n in "ABD")
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)], limit=1)
        cls.account = cls.env["account.analytic.account"].create({"name": "Mig acc", "plan_id": plan.id, "company_id": False})
        cls._line(cls.comp_a, date(2025, 3, 1))
        cls._line(cls.comp_a, date(2025, 5, 10))
        cls._line(cls.comp_b, date(2024, 1, 1))
        cls._line(cls.comp_b, date(2024, 2, 1))
        cls._line(None, date(2024, 6, 1))

    @classmethod
    def _line(cls, company, on_date):
        line = cls.env["account.analytic.line"].create(
            {"name": "mig", "account_id": cls.account.id, "amount": 1, "date": on_date, "company_id": (company or cls.comp_a).id}
        )
        if not company:
            cls.env.flush_all()
            cls.env.cr.execute("UPDATE account_analytic_line SET company_id = NULL WHERE id = %s", (line.id,))
            line.invalidate_recordset()

    def _undated_run(self, company, date_from=None, date_to=None):
        run = self.env["cmc.closing.run"].create(
            {"name": "mig", "method": "direct", "company_id": company.id, "date_from": date(2000, 1, 1), "date_to": date(2000, 1, 2)}
        )
        self.env.flush_all()
        self.env.cr.execute("UPDATE cmc_closing_run SET date_from = %s, date_to = %s WHERE id = %s", (date_from, date_to, run.id))
        run.invalidate_recordset()
        return run

    def _migrate(self):
        self.env.flush_all()
        _load_migrate()(self.env.cr, "19.0.1.0.0")
        self.env.invalidate_all()

    def test_span_with_company_and_companyless_lines(self):
        run_a = self._undated_run(self.comp_a)
        run_b = self._undated_run(self.comp_b)
        self._migrate()
        self.assertEqual((run_a.date_from, run_a.date_to), (date(2024, 6, 1), date(2025, 5, 10)))
        self.assertEqual((run_b.date_from, run_b.date_to), (date(2024, 1, 1), date(2024, 6, 1)))

    def test_half_dated_run_is_never_inverted(self):
        run = self._undated_run(self.comp_a, date_from=date(2026, 1, 1))
        self._migrate()
        self.assertEqual(run.date_from, date(2026, 1, 1))
        self.assertGreaterEqual(run.date_to, run.date_from)

    def test_linked_costing_stays_inside_closing(self):
        if "cmc.costing.run" not in self.env:
            self.skipTest("cmc_full_costing not installed")
        closing = self.env["cmc.closing.run"].create(
            {"name": "mig", "method": "direct", "company_id": self.comp_a.id, "date_from": date(2023, 1, 1), "date_to": date(2026, 12, 31)}
        )
        # a computed closing without recomputing it here
        closing.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        costing = self.env["cmc.costing.run"].create(
            {"name": "mig", "company_id": self.comp_a.id, "closing_run_id": closing.id, "date_from": date(2023, 1, 1), "date_to": date(2026, 12, 31)}
        )
        self.env.flush_all()
        self.env.cr.execute("UPDATE cmc_closing_run SET date_from = NULL, date_to = NULL WHERE id = %s", (closing.id,))
        self._migrate()
        self.assertEqual((closing.date_from, closing.date_to), (costing.date_from, costing.date_to))

    def test_company_without_lines_falls_back_to_create_date(self):
        # company-less lines apply to everyone: assign them away so none is left
        self.env.cr.execute("UPDATE account_analytic_line SET company_id = %s WHERE company_id IS NULL", (self.comp_a.id,))
        run = self._undated_run(self.comp_d)
        self._migrate()
        self.assertEqual(run.date_from, run.create_date.date())
        self.assertEqual(run.date_to, run.create_date.date())
