"""AD-109 - attribute framework in cmc_base (wave B0), option (c): attribute + ordered values,
assigned through one M2M per target model.

Consumers: ABM-02 (VA/NVA, efficiency 1-5), COQ-01 (cost-of-quality category), ABC-05/ABC-06
(hierarchy, cost-object types), PRF-01/PRF-03 (segment/channel layers). AD-106 inv. 3: hierarchies
are attribute values read at compute time (snapshot is the engine's job, not tested here).

CONTRACT
- ``cmc.attribute`` (company-scoped): ``name`` Char required translate, ``code`` Char required,
  ``company_id`` M2o res.company OPTIONAL (False = shared across companies, used by shipped data;
  default env.company), ``sequence`` Integer, ``active`` Boolean, ``target_model_ids`` M2m ``ir.model``
  restricted to models that inherit ``cmc.attribute.mixin`` (ValidationError otherwise),
  ``value_ids`` O2m ``cmc.attribute.value``. Unique index (code, company_id) -> IntegrityError.
  RN-002: a code is unique within what a company sees: a company attribute cannot reuse the code of a
  shared (company-less) attribute and a shared attribute cannot reuse the code of any company attribute
  (ValidationError). Two DIFFERENT companies may still share a code.
- ``cmc.attribute.value``: ``attribute_id`` M2o required ondelete cascade, ``name`` Char required,
  ``code`` Char, ``sequence`` Integer (``_order = "attribute_id, sequence, id"``), ``weight`` Float
  (numeric scales: ordered values with a weight), ``company_id`` related ``attribute_id.company_id``
  stored.
- ``cmc.attribute.mixin`` (AbstractModel): ``cmc_attribute_value_ids`` M2m ``cmc.attribute.value``
  with ``check_company=True``; ONE relation table PER target model (no polymorphic res_model/res_id
  table: AD-109 option (b) is rejected because it cannot be joined in read_group).
  Inherited in cmc_base by ``product.template``, ``res.partner``, ``account.analytic.account``
  (centers) and ``cmc.cost.object``. ``cmc.abc.activity`` gets it in WP-07.
  Constraints (ValidationError): at most ONE value per attribute per record; a value can be assigned
  only to a record whose model is in its attribute's ``target_model_ids``. Company check: a value of a
  company-B attribute on a company-A record -> UserError (``_check_company``).
- ``record._cmc_attribute_value(attribute)`` (mixin, ensure_one): the ``cmc.attribute.value`` of
  ``attribute`` on the record, empty recordset if none. ``attribute`` = record or code (str). RN-002: a code resolves to exactly ONE attribute
  visible to the current companies (``env.companies``: shared or one of them), never to the homonym
  attribute of another company - also under sudo/superuser, where record rules do not filter.
- ``records._cmc_group_by_attribute(attribute)`` (mixin): ``{value_id: [record ids], False: [ids
  without a value]}`` in ONE SQL/_read_group over the relation table - constant query count, whatever
  len(records) (AD-114). Respects the caller's record rules on the values. A code resolves as for
  ``_cmc_attribute_value`` (RN-002).
- Shipped data (noupdate, company_id False), xmlids ``cmc_base.cmc_attribute_<x>``:
  ``value_added`` code VA_CLASS, values (by sequence) codes VA, BVA, NVA;
  ``cost_of_quality`` code COQ, values prevention, appraisal, internal_failure, external_failure,
  error_free; ``efficiency`` code EFFICIENCY, values E1..E5 with weight 1.0..5.0;
  ``customer_segment`` code SEGMENT and ``channel`` code CHANNEL (no shipped values, target
  res.partner).
- Security (AD-115): group_cmc_user read attributes/values and assigns them on records it may write;
  group_cmc_manager create/write/unlink attributes and values; non-CMC users no access (AccessError).
  Global rules ``['|', ('company_id', '=', False), ('company_id', 'parent_of', company_ids)]`` on both
  models, NOT noupdate.
"""

import psycopg2

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

TARGETS = ("product.template", "res.partner", "account.analytic.account", "cmc.cost.object")


@tagged("post_install", "-at_install")
class TestAttributeFramework(TransactionCase):
    """AD-109: one attribute framework, selection-valued, joinable per target model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Attr = cls.env["cmc.attribute"]
        cls.Value = cls.env["cmc.attribute.value"]
        cls.company = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "CMC Attr Co B"})
        cls.model_partner = cls.env["ir.model"]._get("res.partner")
        cls.model_center = cls.env["ir.model"]._get("account.analytic.account")
        cls.model_product = cls.env["ir.model"]._get("product.template")
        cls.model_co = cls.env["ir.model"]._get("cmc.cost.object")
        cls.size = cls.Attr.create({
            "name": "Size",
            "code": "T_SIZE",
            "company_id": cls.company.id,
            "target_model_ids": [(6, 0, (cls.model_partner | cls.model_center | cls.model_product | cls.model_co).ids)],
            "value_ids": [
                (0, 0, {"name": "Small", "code": "S", "sequence": 1, "weight": 1.0}),
                (0, 0, {"name": "Large", "code": "L", "sequence": 2, "weight": 2.0}),
            ],
        })
        cls.small, cls.large = cls.size.value_ids.sorted("sequence")
        cls.partner_only = cls.Attr.create({
            "name": "Partner only",
            "code": "T_PONLY",
            "company_id": cls.company.id,
            "target_model_ids": [(6, 0, cls.model_partner.ids)],
            "value_ids": [(0, 0, {"name": "Yes", "code": "Y"})],
        })
        cls.attr_b = cls.Attr.create({
            "name": "Size B",
            "code": "T_SIZE_B",
            "company_id": cls.company_b.id,
            "target_model_ids": [(6, 0, (cls.model_partner | cls.model_center).ids)],
            "value_ids": [(0, 0, {"name": "B value", "code": "BV"})],
        })
        cls.plan = cls.env["account.analytic.plan"].create({"name": "CMC Attr plan"})
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Attr center", "plan_id": cls.plan.id, "company_id": cls.company.id}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Attr customer"})
        cls.product = cls.env["product.template"].create({"name": "Attr product"})
        cls.cost_object = cls.env["cmc.cost.object"]._cmc_ensure_objects(cls.partner)
        cls.cmc_user = new_test_user(cls.env, login="cmc_attr_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.cmc_manager = new_test_user(cls.env, login="cmc_attr_mgr", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(
            cls.env, login="cmc_attr_out", groups="base.group_user,analytic.group_analytic_accounting"
        )
        cls.user_b = new_test_user(
            cls.env, login="cmc_attr_user_b", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)],
        )

    # ---------------------------------------------------------------- structure

    def test_mixin_on_every_base_target_with_own_relation_table(self):
        # AD-109 (c): M2M per target model, never a polymorphic (res_model, res_id) table
        self.assertIn("cmc.attribute.mixin", self.env)
        relations = set()
        for model in TARGETS:
            field = self.env[model]._fields.get("cmc_attribute_value_ids")
            self.assertTrue(field, "%s must inherit cmc.attribute.mixin" % model)
            self.assertEqual(field.type, "many2many")
            self.assertEqual(field.comodel_name, "cmc.attribute.value")
            relations.add(field.relation)
        self.assertEqual(len(relations), len(TARGETS), "one relation table per target model")
        self.assertNotIn("cmc.attribute.assignment", self.env)

    def test_values_ordered_by_sequence_with_weight(self):
        # AD-109: numeric scales are ordered values with a weight
        self.assertRecordValues(self.size.value_ids, [
            {"code": "S", "weight": 1.0, "company_id": self.company.id},
            {"code": "L", "weight": 2.0, "company_id": self.company.id},
        ])

    def test_code_unique_per_company(self):
        # AD-109: the code is the stable key engines and data refer to
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.Attr.create({"name": "Dup", "code": "T_SIZE", "company_id": self.company.id})
            self.env.flush_all()
        # same code in another company is allowed
        self.Attr.create({"name": "Other co", "code": "T_SIZE", "company_id": self.company_b.id})
        self.env.flush_all()

    def test_target_must_be_a_mixin_model(self):
        # AD-109: only models carrying the mixin can be attribute targets
        with self.assertRaises(ValidationError):
            self.Attr.create({
                "name": "Bad target", "code": "T_BAD", "company_id": self.company.id,
                "target_model_ids": [(6, 0, self.env["ir.model"]._get("res.users").ids)],
            })

    # --------------------------------------------------------------- assignment

    def test_assign_on_every_target(self):
        # AD-109: product template, partner, center and cost object accept attribute values
        for record in (self.product, self.partner, self.center, self.cost_object):
            record.cmc_attribute_value_ids = [(4, self.large.id)]
            self.assertEqual(record._cmc_attribute_value(self.size), self.large, record._name)

    def test_one_value_per_attribute_per_record(self):
        # AD-109: one value per attribute per record (constraint)
        with self.assertRaises(ValidationError):
            self.partner.cmc_attribute_value_ids = [(6, 0, (self.small | self.large).ids)]

    def test_values_of_different_attributes_coexist(self):
        # AD-109: one value per attribute, several attributes per record
        self.partner.cmc_attribute_value_ids = [(6, 0, (self.small | self.partner_only.value_ids).ids)]
        self.assertEqual(self.partner._cmc_attribute_value(self.size), self.small)
        self.assertEqual(self.partner._cmc_attribute_value(self.partner_only), self.partner_only.value_ids)

    def test_value_only_on_target_models_of_its_attribute(self):
        # AD-109: an attribute applies only to the models it declares
        with self.assertRaises(ValidationError):
            self.center.cmc_attribute_value_ids = [(4, self.partner_only.value_ids.id)]

    def test_value_of_other_company_rejected(self):
        # AD-115: company consistency between record and attribute value
        with self.assertRaises(UserError):
            self.center.cmc_attribute_value_ids = [(4, self.attr_b.value_ids.id)]

    def test_helper_empty_and_by_code(self):
        # AD-109: helper returns an empty recordset when unassigned and accepts the code
        self.assertFalse(self.partner._cmc_attribute_value(self.size))
        self.assertEqual(self.partner._cmc_attribute_value(self.size)._name, "cmc.attribute.value")
        self.partner.cmc_attribute_value_ids = [(4, self.small.id)]
        self.assertEqual(self.partner._cmc_attribute_value("T_SIZE"), self.small)

    def test_grouped_helper_maps_values_and_unassigned(self):
        # PRF-01/ABM-02: roll-up by attribute value, unassigned records kept apart (reconciliation)
        partners = self.env["res.partner"].create([{"name": "G%d" % i} for i in range(4)])
        partners[0].cmc_attribute_value_ids = [(4, self.small.id)]
        partners[1].cmc_attribute_value_ids = [(4, self.large.id)]
        partners[2].cmc_attribute_value_ids = [(4, self.large.id)]
        groups = partners._cmc_group_by_attribute(self.size)
        self.assertEqual({k: sorted(v) for k, v in groups.items()}, {
            self.small.id: [partners[0].id],
            self.large.id: sorted(partners[1:3].ids),
            False: [partners[3].id],
        })

    def test_grouped_helper_query_budget_on_volume(self):
        # AD-114: grouping N records costs a constant number of queries (no per-record browse)
        values = self.small | self.large
        small_set = self.env["res.partner"].create([{"name": "Q%d" % i} for i in range(3)])
        big_set = self.env["res.partner"].create(
            [{"name": "V%d" % i, "cmc_attribute_value_ids": [(4, values[i % 2].id)]} for i in range(300)]
        )
        big_set = big_set.with_user(self.cmc_user)
        small_set = small_set.with_user(self.cmc_user)
        small_set._cmc_group_by_attribute(self.size)  # warm-up: ir.rule / ir.model caches
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(2):
            small_set._cmc_group_by_attribute(self.size)
        self.env.invalidate_all()
        with self.assertQueryCount(2):
            groups = big_set._cmc_group_by_attribute(self.size)
        self.assertEqual(len(groups[self.small.id]), 150)
        self.assertEqual(len(groups[self.large.id]), 150)

    def test_rn002_company_attribute_cannot_reuse_shared_code(self):
        # RN-002: a company "CHANNEL" next to the shipped shared one would make the code ambiguous
        with self.assertRaises(ValidationError):
            self.Attr.create({"name": "My channel", "code": "CHANNEL", "company_id": self.company.id})
            self.env.flush_all()

    def test_rn002_shared_attribute_cannot_reuse_company_code(self):
        # RN-002: a shared T_SIZE would be visible to company A together with A's own T_SIZE
        with self.assertRaises(ValidationError):
            self.Attr.create({"name": "Shared size", "code": "T_SIZE", "company_id": False})
            self.env.flush_all()

    def _homonym_b(self):
        # RN-002: company B's own T_SIZE (allowed: different company) carried by a shared partner
        size_b = self.Attr.create({
            "name": "Size (B)", "code": "T_SIZE", "company_id": self.company_b.id,
            "target_model_ids": [(6, 0, self.model_partner.ids)],
            "value_ids": [(0, 0, {"name": "B small", "code": "BS"})],
        })
        partner_b = self.env["res.partner"].create({"name": "Attr shared partner B", "company_id": False})
        partner_b.cmc_attribute_value_ids = [(4, size_b.value_ids.id)]
        return size_b, partner_b

    def test_rn002_code_resolves_to_the_company_attribute(self):
        # RN-002: in company A "T_SIZE" is A's attribute, in company B it is B's - never the other one
        size_b, partner_b = self._homonym_b()
        env_a = self.env(context=dict(self.env.context, allowed_company_ids=self.company.ids))
        env_b = self.env(context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        self.assertFalse(partner_b.with_env(env_a)._cmc_attribute_value("T_SIZE"))
        self.assertEqual(partner_b.with_env(env_b)._cmc_attribute_value("T_SIZE"), size_b.value_ids)

    def test_rn002_grouped_helper_resolves_code_per_company(self):
        # RN-002: grouping by code in company A ignores B's homonym values even as superuser
        size_b, partner_b = self._homonym_b()
        partner_a = self.env["res.partner"].create({"name": "Attr partner A"})
        partner_a.cmc_attribute_value_ids = [(4, self.small.id)]
        env_a = self.env(context=dict(self.env.context, allowed_company_ids=self.company.ids))
        groups = (partner_a | partner_b).with_env(env_a)._cmc_group_by_attribute("T_SIZE")
        self.assertEqual(groups, {self.small.id: [partner_a.id], False: [partner_b.id]})
        env_b = self.env(context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        groups = (partner_a | partner_b).with_env(env_b)._cmc_group_by_attribute("T_SIZE")
        self.assertEqual(groups, {size_b.value_ids.id: [partner_b.id], False: [partner_a.id]})

    # -------------------------------------------------------------- shipped data

    def test_shipped_value_added(self):
        # ABM-02: VA / BVA / NVA classification
        attr = self.env.ref("cmc_base.cmc_attribute_value_added")
        self.assertFalse(attr.company_id)
        self.assertEqual(attr.value_ids.sorted("sequence").mapped("code"), ["VA", "BVA", "NVA"])

    def test_shipped_cost_of_quality(self):
        # COQ-01: prevention / appraisal / internal failure / external failure (+ error-free)
        attr = self.env.ref("cmc_base.cmc_attribute_cost_of_quality")
        self.assertEqual(attr.code, "COQ")
        self.assertEqual(
            attr.value_ids.sorted("sequence").mapped("code"),
            ["prevention", "appraisal", "internal_failure", "external_failure", "error_free"],
        )

    def test_shipped_efficiency_scale_weighted(self):
        # ABM-02: efficiency 1-5 is an ordered, weighted scale
        attr = self.env.ref("cmc_base.cmc_attribute_efficiency")
        values = attr.value_ids.sorted("sequence")
        self.assertEqual(values.mapped("code"), ["E1", "E2", "E3", "E4", "E5"])
        self.assertEqual(values.mapped("weight"), [1.0, 2.0, 3.0, 4.0, 5.0])

    def test_shipped_segment_and_channel_on_partner(self):
        # PRF-01/ABC-06: segment and channel as attributes (no dependency on sale/crm, AD-102)
        for xmlid, code in (("cmc_attribute_customer_segment", "SEGMENT"), ("cmc_attribute_channel", "CHANNEL")):
            attr = self.env.ref("cmc_base.%s" % xmlid)
            self.assertEqual(attr.code, code)
            self.assertFalse(attr.company_id)
            self.assertIn("res.partner", attr.target_model_ids.mapped("model"))

    # ------------------------------------------------------------------ security

    def test_user_reads_and_assigns_on_center(self):
        # AD-115: a CMC user reads attributes and tags the centers he may edit
        Attr = self.Attr.with_user(self.cmc_user)
        self.assertIn(self.size, Attr.search([("code", "=", "T_SIZE")]))
        center = self.center.with_user(self.cmc_user)
        center.write({"cmc_attribute_value_ids": [(4, self.small.id)]})
        self.assertEqual(center._cmc_attribute_value(self.size), self.small)

    def test_user_cannot_configure(self):
        # AD-115: configuring attributes is a manager task
        with self.assertRaises(AccessError):
            self.Attr.with_user(self.cmc_user).create({"name": "U", "code": "T_U", "company_id": self.company.id})
        with self.assertRaises(AccessError):
            self.size.with_user(self.cmc_user).write({"name": "renamed"})
        with self.assertRaises(AccessError):
            self.Value.with_user(self.cmc_user).create({"attribute_id": self.size.id, "name": "XL"})

    def test_manager_configures(self):
        # AD-115: manager creates attributes and values
        attr = self.Attr.with_user(self.cmc_manager).create({
            "name": "Strategy fit", "code": "T_FIT", "company_id": self.company.id,
            "target_model_ids": [(6, 0, self.model_product.ids)],
        })
        value = self.Value.with_user(self.cmc_manager).create({"attribute_id": attr.id, "name": "High", "weight": 3.0})
        value.with_user(self.cmc_manager).write({"name": "Very high"})
        self.assertEqual(attr.value_ids, value)

    def test_outsider_has_no_access(self):
        # AD-115: non-CMC users do not see the attribute configuration
        with self.assertRaises(AccessError):
            self.Attr.with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.Value.with_user(self.outsider).search([])

    def test_company_rule_isolates_attributes(self):
        # AD-115: company B user sees shared + company B attributes, never company A ones
        visible = self.Attr.with_user(self.user_b).search([])
        self.assertIn(self.attr_b, visible)
        self.assertNotIn(self.size, visible)
        self.assertIn(self.env.ref("cmc_base.cmc_attribute_channel"), visible)
        self.assertNotIn(self.small, self.Value.with_user(self.user_b).search([]))

    def test_company_rules_not_noupdate(self):
        # AD-115: rules must be updatable on installed databases
        for xmlid in ("cmc_base.cmc_attribute_company_rule", "cmc_base.cmc_attribute_value_company_rule"):
            rule = self.env.ref(xmlid)
            data = self.env["ir.model.data"].search([("module", "=", "cmc_base"), ("name", "=", xmlid.split(".")[1])])
            self.assertTrue(rule["global"])
            self.assertFalse(data.noupdate, xmlid)
