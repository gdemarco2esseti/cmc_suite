"""RN-001 - backfill of the finished-product cost object on manufacturing lines booked before the bridge (AD-106).

CONTRACT
- Migration ``cmc_mrp/migrations/19.0.1.4.0/post-migrate.py`` ``migrate(cr, version)`` (manifest bumped to
  >= 19.0.1.4.0), same rule as the live bridge (``test_cost_object_stamp``): for every EXISTING MO WITH
  ``cmc_center_id``, its MP lines (``account.analytic.line.cmc_mrp_move_id`` = one of its raw moves, created or
  adopted) and the workcenter lines of its work orders (``mrp.workorder.wc_analytic_account_line_ids``) that
  have no ``cmc_cost_object_id`` get the ``cmc.cost.object`` of type ``product`` of the FINISHED product
  (``mrp.production.product_id``) in the MO's company, creating missing objects.
- MOs without ``cmc_center_id`` (RN-003) are untouched; an already-stamped line is never overwritten;
  ``product_id`` of an MP line stays the raw material. Idempotent. Batched per company: the query count does
  not grow with the number of MOs/lines.
"""

import importlib.util
import os

from odoo.modules import get_module_path
from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import parse_version

MIGRATION_VERSION = "19.0.1.4.0"


def _migration_path():
    return os.path.join(get_module_path("cmc_mrp"), "migrations", MIGRATION_VERSION, "post-migrate.py")


def _load_migrate():
    # why: the migrations folder is named after a version, not importable
    spec = importlib.util.spec_from_file_location("cmc_mrp_post_migrate_1_4_0", _migration_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestMrpBackfill(TransactionCase):
    """RN-001: manufacturing cost booked before the stamp existed belongs to the finished product too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CO = cls.env["cmc.cost.object"]
        cls.AA = cls.env["account.analytic.account"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.AA.create({"name": "Reparto backfill", "plan_id": cls.plan.id, "cmc_center_type": "final"})
        cls.categ = cls.env["product.category"].create(
            {"name": "Backfill periodic", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.workcenter = cls.env["mrp.workcenter"].create(
            {"name": "WC backfill", "costs_hour": 30.0, "analytic_distribution": {str(cls.center.id): 100}}
        )

    # ---- producers: the real MO flow books the lines, the live bridge stamps them (blanked below) -------

    def _mo(self, name, center=None, company=None, with_operation=False):
        env = self.env if not company else self.env["mrp.production"].with_company(company).env
        comp = env["product.product"].create(
            {"name": "Comp %s" % name, "is_storable": True, "categ_id": self.categ.id, "standard_price": 7.0}
        )
        finished = env["product.product"].create({"name": "Fin %s" % name, "is_storable": True, "categ_id": self.categ.id})
        bom_vals = {
            "product_tmpl_id": finished.product_tmpl_id.id,
            "product_qty": 1.0,
            "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 3.0})],
        }
        if with_operation:
            bom_vals["operation_ids"] = [(0, 0, {"name": "Assembly", "workcenter_id": self.workcenter.id, "time_cycle_manual": 60.0})]
        bom = env["mrp.bom"].create(bom_vals)
        vals = {"product_id": finished.id, "product_qty": 1.0, "bom_id": bom.id}
        if center is not False:
            vals["cmc_center_id"] = (center or self.center).id
        if company:
            vals["company_id"] = company.id
        return env["mrp.production"].create(vals)

    def _mark_done(self, mo, duration=None):
        mo.action_confirm()
        if duration is not None:
            mo.workorder_ids.duration = duration
        mo.qty_producing = 1.0
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()
        return mo

    def _done(self, name, **kw):
        duration = 60.0 if kw.get("with_operation") else None
        return self._mark_done(self._mo(name, **kw), duration=duration)

    @staticmethod
    def _lines(mo):
        return mo.move_raw_ids.cmc_mrp_analytic_line_ids | mo.workorder_ids.wc_analytic_account_line_ids

    def _blank(self, lines):
        # why SQL: reproduce the lines booked before the stamp existed, which never went through the bridge
        self.assertTrue(lines, "precondition: the MO booked analytic lines")
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_cost_object_id = NULL WHERE id IN %s", (tuple(lines.ids),))
        self.env.invalidate_all()

    def _object_of(self, product, company):
        return self.CO.search([("res_model", "=", "product.product"), ("res_id", "=", product.id), ("company_id", "=", company.id)])

    def _migrate(self):
        self.assertTrue(os.path.isfile(_migration_path()), "cmc_mrp/migrations/%s/post-migrate.py is missing" % MIGRATION_VERSION)
        self.env.flush_all()
        _load_migrate()(self.env.cr, "19.0.1.3.0")
        self.env.invalidate_all()

    def test_manifest_version_bumped(self):
        # RN-001: the migration only runs if the installed version moves to 19.0.1.4.0
        self.assertGreaterEqual(parse_version(get_manifest("cmc_mrp")["version"]), parse_version(MIGRATION_VERSION))

    def test_mp_line_backfilled_with_finished_product(self):
        # RN-001 / AD-106: old MP cost belongs to the finished product, product_id stays the raw material
        mo = self._done("bf-mp")
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self._blank(line)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, self._object_of(mo.product_id, mo.company_id))
        self.assertTrue(line.cmc_cost_object_id)
        self.assertEqual(line.product_id, mo.move_raw_ids.product_id)

    def test_workcenter_line_backfilled(self):
        # RN-001 / AD-106: old MOD on the workcenter belongs to the finished product too
        mo = self._done("bf-wc", with_operation=True)
        lines = mo.workorder_ids.wc_analytic_account_line_ids
        self._blank(lines)
        self._migrate()
        self.assertEqual(lines.cmc_cost_object_id, self._object_of(mo.product_id, mo.company_id))
        self.assertTrue(lines.cmc_cost_object_id)

    def test_missing_object_created(self):
        # RN-001: a product never costed before has no object yet: the migration creates it
        mo = self._done("bf-new", with_operation=True)
        lines = self._lines(mo)
        self._blank(lines)
        self.env.cr.execute(
            "DELETE FROM cmc_cost_object WHERE res_model = 'product.product' AND res_id = %s", (mo.product_id.id,)
        )
        self.env.invalidate_all()
        self.assertFalse(self._object_of(mo.product_id, mo.company_id), "precondition: no object")
        self._migrate()
        obj = self._object_of(mo.product_id, mo.company_id)
        self.assertEqual(len(obj), 1)
        self.assertEqual(lines.cmc_cost_object_id, obj)

    def test_mo_without_center_untouched(self):
        # RN-001 / RN-003: an MO without CMC center is outside CMC product costing, before and after the backfill
        mo = self._done("bf-nocenter", center=False, with_operation=True)
        lines = mo.workorder_ids.wc_analytic_account_line_ids
        self.assertTrue(lines, "precondition: core books the workcenter time")
        self._migrate()
        self.assertFalse(lines.cmc_cost_object_id)

    def test_existing_stamp_not_overwritten(self):
        # RN-001: a line already stamped (by a rule or by hand) keeps its object
        mo = self._done("bf-keep")
        other = self._done("bf-other").move_raw_ids.cmc_mrp_analytic_line_ids.cmc_cost_object_id
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_cost_object_id = %s WHERE id = %s", (other.id, line.id))
        self.env.invalidate_all()
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, other)

    def test_object_in_mo_company(self):
        # RN-001 / AD-115: batched per company, the object of a company-B order lives in company B
        company_b = self.env["res.company"].create({"name": "MRP backfill B"})
        self.env.user.company_ids = [(4, company_b.id)]
        center_b = self.AA.create(
            {"name": "Reparto backfill B", "plan_id": self.plan.id, "cmc_center_type": "final", "company_id": company_b.id}
        )
        mo_b = self._done("bf-b", center=center_b, company=company_b)
        mo_a = self._done("bf-a")
        self._blank(self._lines(mo_b) | self._lines(mo_a))
        self._migrate()
        line_b = mo_b.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertEqual(line_b.cmc_cost_object_id.company_id, company_b)
        self.assertEqual(line_b.cmc_cost_object_id.res_id, mo_b.product_id.id)
        self.assertEqual(mo_a.move_raw_ids.cmc_mrp_analytic_line_ids.cmc_cost_object_id.company_id, mo_a.company_id)

    def test_idempotent(self):
        # RN-001: running twice changes nothing and creates no duplicate object
        mo = self._done("bf-twice", with_operation=True)
        lines = self._lines(mo)
        self._blank(lines)
        self._migrate()
        stamped = lines.cmc_cost_object_id
        self._migrate()
        self.assertEqual(lines.cmc_cost_object_id, stamped)
        self.assertEqual(len(self._object_of(mo.product_id, mo.company_id)), 1)

    def test_query_count_independent_of_volume(self):
        # RN-001 / AD-114: the backfill runs on the whole manufacturing history: batched, never one query per MO/line
        self._migrate()  # clean slate: anything pre-existing in the database is stamped now

        def count(n):
            mos = self.env["mrp.production"]
            for i in range(n):
                mos |= self._done("bf-vol-%s-%s" % (n, i), with_operation=True)
            lines = mos.move_raw_ids.cmc_mrp_analytic_line_ids | mos.workorder_ids.wc_analytic_account_line_ids
            self._blank(lines)
            before = self.env.cr.sql_log_count
            self._migrate()
            spent = self.env.cr.sql_log_count - before
            self.assertEqual(len(lines.filtered("cmc_cost_object_id")), len(lines))
            return spent

        small = count(2)
        large = count(10)
        self.assertLessEqual(large, small + 4, "10 orders must cost the same queries as 2")
