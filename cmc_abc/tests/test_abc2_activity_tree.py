"""WP-07 / ABC-01, ABC-05, ABC-08 (master part) - activity dictionary, cost hierarchy, attributes.

CONTRACT (see abc2_common for the whole WP)
- ``cmc.abc.activity``: ``node_type`` process / activity / task, ``parent_id`` / ``child_ids``,
  ``cost_level`` unit / batch / product / customer / facility, ``owner_id``, ``is_sustaining`` (stored,
  computed from ``cost_level == 'facility'``, editable). ValidationError: process with parent, activity
  under a non-process, task under a non-activity (no cycle possible), statistical driver, level mismatch with
  ``driver_id.cmc_abc_level``. Inherits ``cmc.attribute.mixin``; shipped VA_CLASS targets it.
- Roll-up: ``run._cmc_process_costs()`` -> {process id: Σ stage-1 cost of its activities} (higher level =
  Σ lower level, no double count of A->A); ``run._cmc_cost_by_level()``; ``run._cmc_activity_cost_by_attribute``.
Fixture numbers: stage 1 SETUP 3400, MACH 6600, QC 2500, ADMIN 1000, CUST 1000, MGMT 2000.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import new_test_user, tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2ActivityTree(Abc2Case):
    def test_abc01_dictionary_hierarchy(self):
        # ABC-01: process > activity > task, each node knows its parent and children
        self.assertEqual(self.pr_mfg.child_ids, self.setup | self.mach | self.qc)
        self.assertEqual(self.load.parent_id, self.mach)
        self.assertEqual(self.load.node_type, "task")
        self.assertEqual(self.pr_mfg.node_type, "process")

    def test_abc01_owner(self):
        # ABC-01: every activity has an accountable owner
        owner = new_test_user(self.env, login="wp07_owner", groups="cmc_base.group_cmc_user")
        self.mach.owner_id = owner
        self.assertEqual(self.mach.owner_id, owner)

    def test_abc01_process_cannot_have_parent(self):
        # ABC-01: a process is the top of the tree
        with self.assertRaises(ValidationError):
            self.pr_sup.parent_id = self.pr_mfg

    def test_abc01_task_needs_activity_parent(self):
        # ABC-01: a task describes an activity, never a process directly
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abc.activity"].create(
                {"name": "Bad task", "code": "WP07BADT", "node_type": "task", "parent_id": self.pr_mfg.id}
            )

    def test_abc01_activity_needs_process_parent(self):
        # ABC-01: an activity sits under a process (or nowhere), not under another activity
        with self.assertRaises(ValidationError):
            self.qc.parent_id = self.mach

    def test_abc01_process_rollup_is_sum_of_activities(self):
        # ABC-01: higher level = Σ lower level, on stage-1 cost (A->A reassignment not double counted)
        run = self._main_run()
        costs = run._cmc_process_costs()
        self.assertAlmostEqual(costs[self.pr_mfg.id], 12500.0, places=2)  # 3400 + 6600 + 2500
        self.assertAlmostEqual(costs[self.pr_sup.id], 4000.0, places=2)  # 1000 + 1000 + 2000
        self.assertAlmostEqual(sum(costs.values()), run.total_activity_cost, places=2)

    def test_abc05_level_matching_driver(self):
        # ABC-05: a unit-level activity cannot be measured with a batch-level driver (setups != hours)
        with self.assertRaises(ValidationError):
            self.mach.driver_id = self.setups

    def test_abc05_facility_is_sustaining_by_default(self):
        # ABC-05/ABC-10: facility-sustaining activities are business-sustaining unless told otherwise
        self.assertTrue(self.mgmt.is_sustaining)
        self.assertFalse(self.mach.is_sustaining)
        self.admin.is_sustaining = True  # configurable: an SG&A activity marked by hand
        self.assertTrue(self.admin.is_sustaining)

    def test_abc05_cost_by_level(self):
        # ABC-05 (Kaplan Ch.6): % of expenses per hierarchy level
        run = self._main_run()
        by_level = run._cmc_cost_by_level()
        self.assertAlmostEqual(by_level["unit"], 6600.0, places=2)
        self.assertAlmostEqual(by_level["batch"], 5900.0, places=2)  # SETUP 3400 + QC 2500
        self.assertAlmostEqual(by_level["customer"], 1000.0, places=2)
        self.assertAlmostEqual(by_level["facility"], 2000.0, places=2)
        self.assertAlmostEqual(by_level[False], 1000.0, places=2)  # support activity without level

    def test_abc08_statistical_driver_rejected_on_activity(self):
        # AD-107 inv.3: an activity allocates, so its driver must be an allocation base
        with self.assertRaises(ValidationError):
            self.qc.driver_id = self.produced

    def test_abc01_activity_carries_attributes(self):
        # AD-109: VA/NVA lives on the activity through the mixin (shipped attribute targets it)
        va_class = self.env.ref("cmc_base.cmc_attribute_value_added")
        self.assertIn("cmc.abc.activity", va_class.target_model_ids.mapped("model"))
        nva = self.env.ref("cmc_base.cmc_attribute_value_nva")
        self.qc.cmc_attribute_value_ids = [(6, 0, nva.ids)]
        self.assertEqual(self.qc._cmc_attribute_value("VA_CLASS"), nva)

    def test_abc01_cost_by_attribute(self):
        # ABC-01/AD-109: activity cost reported by attribute value (NVA = setup + inspection)
        va = self.env.ref("cmc_base.cmc_attribute_value_va")
        bva = self.env.ref("cmc_base.cmc_attribute_value_bva")
        nva = self.env.ref("cmc_base.cmc_attribute_value_nva")
        for activity, value in ((self.setup, nva), (self.qc, nva), (self.mach, va), (self.admin, bva),
                                (self.cust, bva), (self.mgmt, bva)):
            activity.cmc_attribute_value_ids = [(6, 0, value.ids)]
        run = self._main_run()
        by_value = run._cmc_activity_cost_by_attribute("VA_CLASS")
        self.assertAlmostEqual(by_value[nva.id], 5900.0, places=2)
        self.assertAlmostEqual(by_value[va.id], 6600.0, places=2)
        self.assertAlmostEqual(by_value[bva.id], 4000.0, places=2)
