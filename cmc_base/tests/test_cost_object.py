"""AD-106 - cost-object registry in cmc_base, stamped on the analytic line (wave A0).

CONTRACT
- Model ``cmc.cost.object`` (company-scoped, global rule ``company_id parent_of company_ids``):
  ``object_type`` Selection required, base keys ``product``, ``customer``, ``supplier``, ``project``,
  ``unassigned`` (extensible with ``selection_add``); ``res_model`` Char required;
  ``res_id`` Many2oneReference(model_field="res_model") required; ``company_id`` M2o required;
  ``display_name`` = display name of the referenced record. NO ``parent_id`` (inv. 3).
  Unique index on (object_type, res_model, res_id, company_id) -> IntegrityError on a duplicate.
  ACL: group_cmc_user read; group_cmc_manager read/write; no access for non-CMC users. Objects are
  created by the system (lazily, in sudo) so a non-CMC user booking a line still gets it stamped.
- ``cmc.cost.object._cmc_ensure_objects(records, company=None, object_type=None)`` (api.model):
  returns a ``cmc.cost.object`` recordset ALIGNED with ``records`` (same length/order), creating the
  missing ones in ONE batch, idempotent. Default company = env.company; default type from the model
  (product.product -> product, res.partner -> customer, project.project -> project).
- ``account.analytic.line.cmc_cost_object_id`` M2o ``cmc.cost.object``, stored, indexed.
- Migration ``cmc_base/migrations/19.0.1.3.0/post-migrate.py`` ``migrate(cr, version)`` (manifest bumped
  to >= 19.0.1.3.0): every line with a ``cmc_cost_object_ref`` and no ``cmc_cost_object_id`` gets the
  object of the referenced record in the LINE's company. Mappable: product.product, res.partner
  (customer), project.project, product.template with exactly one variant (-> that variant).
  Unmappable (cost center, sale line, deleted record, multi-variant template): left empty, one WARNING
  logged per unmappable value. Already-stamped lines are never overwritten. Idempotent.
"""

import importlib.util
import os
from datetime import date

import psycopg2

from odoo.exceptions import AccessError
from odoo.modules import get_module_path
from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger, parse_version

MIGRATION_VERSION = "19.0.1.3.0"


def _migration_path():
    return os.path.join(get_module_path("cmc_base"), "migrations", MIGRATION_VERSION, "post-migrate.py")


def _load_migrate():
    # why: the migrations folder is named after a version, not importable
    spec = importlib.util.spec_from_file_location("cmc_base_post_migrate_1_3_0", _migration_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestCostObjectRegistry(TransactionCase):
    """AD-106: one key for every cost object, groupable, company-scoped, created lazily."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CO = cls.env["cmc.cost.object"]
        cls.company = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "CMC CO Co B"})
        cls.product = cls.env["product.product"].create({"name": "Finished CO"})
        cls.partner = cls.env["res.partner"].create({"name": "Customer CO"})
        cls.user = new_test_user(cls.env, login="cmc_co_user", groups="cmc_base.group_cmc_user")
        cls.outsider = new_test_user(
            cls.env, login="cmc_co_outsider", groups="base.group_user,analytic.group_analytic_accounting"
        )

    def test_ensure_creates_typed_object(self):
        # AD-106: type + reference + company
        obj = self.CO._cmc_ensure_objects(self.product)
        self.assertRecordValues(
            obj,
            [{"object_type": "product", "res_model": "product.product", "res_id": self.product.id,
              "company_id": self.company.id}],
        )
        self.assertIn("Finished CO", obj.display_name)

    def test_ensure_is_idempotent(self):
        # AD-106 inv.(1): one object per (type, record, company)
        first = self.CO._cmc_ensure_objects(self.product)
        second = self.CO._cmc_ensure_objects(self.product)
        self.assertEqual(first, second)
        self.assertEqual(self.CO.search_count([("res_model", "=", "product.product"), ("res_id", "=", self.product.id)]), 1)

    def test_ensure_is_aligned_with_input(self):
        # AD-106: callers zip records and objects
        other = self.env["product.product"].create({"name": "Other CO"})
        objs = self.CO._cmc_ensure_objects(other + self.product)
        self.assertEqual(objs.mapped("res_id"), [other.id, self.product.id])

    def test_partner_defaults_to_customer_supplier_explicit(self):
        # AD-106: the same partner may be a customer object and a supplier object
        customer = self.CO._cmc_ensure_objects(self.partner)
        supplier = self.CO._cmc_ensure_objects(self.partner, object_type="supplier")
        self.assertEqual(customer.object_type, "customer")
        self.assertEqual(supplier.object_type, "supplier")
        self.assertNotEqual(customer, supplier)

    def test_object_per_company(self):
        # AD-106/AD-115: a shared product gets one object per company that books on it
        obj_a = self.CO._cmc_ensure_objects(self.product)
        obj_b = self.CO._cmc_ensure_objects(self.product, company=self.company_b)
        self.assertNotEqual(obj_a, obj_b)
        self.assertEqual(obj_b.company_id, self.company_b)

    def test_unique_index_at_source(self):
        # AD-106 inv.(1): unicity enforced by the database, not by Python only
        self.CO._cmc_ensure_objects(self.product)
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.CO.create({"object_type": "product", "res_model": "product.product", "res_id": self.product.id,
                            "company_id": self.company.id})
            self.env.flush_all()

    def test_no_hierarchy_on_the_object(self):
        # AD-106 inv.(3): hierarchies are attributes snapshotted on results, never a stored parent
        self.assertNotIn("parent_id", self.CO._fields)

    def test_analytic_line_field_is_stored_and_indexed(self):
        # AD-106: groupable in read_group/SQL, one join for every engine
        field = self.env["account.analytic.line"]._fields["cmc_cost_object_id"]
        self.assertEqual(field.comodel_name, "cmc.cost.object")
        self.assertTrue(field.store)
        self.assertTrue(field.index)

    def test_cost_groupable_by_object(self):
        # AD-106: a Reference is not groupable, the M2o is
        obj = self.CO._cmc_ensure_objects(self.product)
        plan = self.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        center = self.env["account.analytic.account"].create({"name": "CO center", "plan_id": plan.id})
        self.env["account.analytic.line"].create(
            [{"name": "c", "account_id": center.id, "amount": -100.0, "cmc_cost_object_id": obj.id},
             {"name": "c", "account_id": center.id, "amount": -50.0, "cmc_cost_object_id": obj.id}]
        )
        rows = self.env["account.analytic.line"]._cmc_cost_read_group(
            [("account_id", "=", center.id)], groupby=["cmc_cost_object_id"]
        )
        self.assertEqual(rows, [(obj, 150.0)])

    def test_ensure_batched(self):
        # AD-114 (4): lazy creation is one batch, not one query per record
        products_small = self.env["product.product"].create([{"name": "S%s" % i} for i in range(3)])
        products_large = self.env["product.product"].create([{"name": "L%s" % i} for i in range(40)])

        def count(records):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            self.CO._cmc_ensure_objects(records)
            self.env.flush_all()
            return self.env.cr.sql_log_count - before

        small = count(products_small)
        large = count(products_large)
        self.assertLessEqual(large, small + 3, "ensuring 40 objects must not cost a query per record")

    # ---- security ----------------------------------------------------------------

    def test_user_reads_own_company_objects_only(self):
        # AD-115: company rule on the registry
        obj_a = self.CO._cmc_ensure_objects(self.product)
        obj_b = self.CO._cmc_ensure_objects(self.product, company=self.company_b)
        visible = self.CO.with_user(self.user).search([])
        self.assertIn(obj_a, visible)
        self.assertNotIn(obj_b, visible)

    def test_user_cannot_create_objects_by_hand(self):
        # AD-106: objects are created lazily by the system, users only read them
        with self.assertRaises(AccessError):
            self.CO.with_user(self.user).create(
                {"object_type": "customer", "res_model": "res.partner", "res_id": self.partner.id,
                 "company_id": self.company.id}
            )

    def test_outsider_cannot_read_objects(self):
        # AD-115: no CMC group, no CMC registry
        self.CO._cmc_ensure_objects(self.product)
        with self.assertRaises(AccessError):
            self.CO.with_user(self.outsider).search([])


@tagged("post_install", "-at_install")
class TestCostObjectMigration(TransactionCase):
    """AD-106: the informational Reference cmc_cost_object_ref is migrated onto the registry M2o."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.CO = cls.env["cmc.cost.object"]
        cls.company_b = cls.env["res.company"].create({"name": "CMC Mig Co B"})
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Migration center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.center_b = cls.env["account.analytic.account"].create(
            {"name": "Migration center B", "plan_id": plan.id, "company_id": cls.company_b.id}
        )
        cls.product = cls.env["product.product"].create({"name": "Mig product"})
        cls.partner = cls.env["res.partner"].create({"name": "Mig customer"})

    def _line(self, ref, **kw):
        vals = {"name": "old", "account_id": self.center.id, "amount": -10.0, "date": date(2026, 3, 15),
                "cmc_cost_object_ref": ref}
        vals.update(kw)
        return self.AL.create(vals)

    def _migrate(self):
        self.assertTrue(os.path.isfile(_migration_path()), "cmc_base/migrations/%s/post-migrate.py is missing" % MIGRATION_VERSION)
        _load_migrate()(self.env.cr, "19.0.1.2.0")
        self.env.invalidate_all()

    def test_manifest_version_bumped(self):
        # AD-106: the migration only runs if the installed version moves to 19.0.1.3.0
        self.assertGreaterEqual(parse_version(get_manifest("cmc_base")["version"]), parse_version(MIGRATION_VERSION))

    def test_product_ref_migrated(self):
        # AD-106: product reference -> product object of the line's company
        line = self._line("product.product,%d" % self.product.id)
        self._migrate()
        self.assertRecordValues(
            line.cmc_cost_object_id,
            [{"object_type": "product", "res_model": "product.product", "res_id": self.product.id,
              "company_id": line.company_id.id}],
        )

    def test_partner_ref_migrated_as_customer(self):
        # AD-106: the old Reference labelled partners "Customer"
        line = self._line("res.partner,%d" % self.partner.id)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id.object_type, "customer")
        self.assertEqual(line.cmc_cost_object_id.res_id, self.partner.id)

    def test_single_variant_template_mapped_to_variant(self):
        # AD-106: a template with one variant is that variant
        line = self._line("product.template,%d" % self.product.product_tmpl_id.id)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id.res_model, "product.product")
        self.assertEqual(line.cmc_cost_object_id.res_id, self.product.id)

    def test_project_ref_migrated(self):
        # AD-106: project reference -> project object
        if "project.project" not in self.env:
            self.skipTest("project not installed")
        project = self.env["project.project"].create({"name": "Mig project"})
        line = self._line("project.project,%d" % project.id)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id.object_type, "project")
        self.assertEqual(line.cmc_cost_object_id.res_id, project.id)

    def test_object_in_line_company(self):
        # AD-115: the migrated object belongs to the company of the line, not to the migrating user
        line = self._line("product.product,%d" % self.product.id, account_id=self.center_b.id, company_id=self.company_b.id)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id.company_id, self.company_b)

    def test_unmappable_ref_left_empty_and_logged(self):
        # AD-106: a cost center is no cost object of the registry: keep the line, log the value
        line = self._line("account.analytic.account,%d" % self.center.id)
        with self.assertLogs(level="WARNING") as logs:
            self._migrate()
        self.assertFalse(line.cmc_cost_object_id)
        self.assertTrue(any("account.analytic.account" in message for message in logs.output))

    def test_stamped_line_not_overwritten(self):
        # AD-106: a bridge-stamped object wins over the informational Reference
        other = self.env["product.product"].create({"name": "Bridge stamped"})
        stamped = self.CO._cmc_ensure_objects(other)
        line = self._line("product.product,%d" % self.product.id, cmc_cost_object_id=stamped.id)
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, stamped)

    def test_migration_idempotent(self):
        # AD-106 inv.(1): running twice creates no second object
        line = self._line("product.product,%d" % self.product.id)
        self._migrate()
        first = line.cmc_cost_object_id
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, first)
        self.assertEqual(
            self.CO.search_count([("res_model", "=", "product.product"), ("res_id", "=", self.product.id),
                                  ("company_id", "=", line.company_id.id)]),
            1,
        )

    def test_lines_without_ref_untouched(self):
        # AD-106: nothing to migrate, nothing stamped
        line = self.AL.create({"name": "plain", "account_id": self.center.id, "amount": -5.0})
        self._migrate()
        self.assertFalse(line.cmc_cost_object_id)
