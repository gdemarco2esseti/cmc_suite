"""WP-15 / PRJ-03 - change requests tracked outside the baseline; an approved CR re-baselines on a version copy.

CONTRACT: see ``evm_common`` (``cmc.evm.change.request``, ``action_approve`` / ``action_reject``).
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .evm_common import APR, EvmCase


@tagged("post_install", "-at_install")
class TestEvmChange(EvmCase):
    def _cr(self, baseline, **vals):
        return self.env["cmc.evm.change.request"].create({
            "name": "CR-01 extra welding", "baseline_id": baseline.id, "task_id": self.t2.id,
            "date": date(2026, 4, 10), "cost_impact": 1500.0, **vals,
        })

    def test_prj03_draft_change_request_leaves_the_baseline_alone(self):
        # PRJ-03 (PCM Ch1): a change is a separate cost line until re-baselining, never taken from the baseline
        baseline = self._baseline()
        cr = self._cr(baseline)
        self.assertEqual(cr.state, "draft")
        self.assertEqual(baseline.change_request_ids, cr)
        self.assertEqual(baseline.state, "done")
        self.assertAlmostEqual(baseline.bac, 32200.0, places=2)
        self.assertFalse(cr.new_baseline_id)

    def test_prj03_approved_change_request_rebaselines_on_a_copy(self):
        # PRJ-03: approval copies the baseline into a new draft, adds the change, re-plans; the old one stays
        baseline = self._baseline()
        cr = self._cr(baseline)
        cr.action_approve()
        new = cr.new_baseline_id
        self.assertEqual(cr.state, "approved")
        self.assertRecordValues(new, [{
            "parent_id": baseline.id, "state": "draft", "version_type": "baseline", "project_id": self.prj.id,
            "bac": 33700.0, "contingency_reserve": 2400.0,
        }])
        self.assertEqual(len(new.estimate_line_ids), 4)
        added = new.estimate_line_ids.filtered(lambda line: line.name == "CR-01 extra welding")
        self.assertRecordValues(added, [{"task_id": self.t2.id, "material_cost": 1500.0, "date_start": APR, "date_end": APR}])
        april_t2 = new.baseline_line_ids.filtered(lambda r: r.task_id == self.t2 and r.date == APR)
        self.assertAlmostEqual(sum(april_t2.mapped("planned_value")), 9200.0, places=2)
        # the reference baseline is untouched
        self.assertEqual(baseline.state, "done")
        self.assertAlmostEqual(baseline.bac, 32200.0, places=2)
        self.assertEqual(len(baseline.estimate_line_ids), 3)

    def test_prj03_new_baseline_approved_by_manager(self):
        # PRJ-03: the re-baseline becomes the reference only once approved
        cr = self._cr(self._baseline())
        cr.action_approve()
        cr.new_baseline_id.action_approve()
        self.assertEqual(cr.new_baseline_id.state, "done")
        self.assertAlmostEqual(cr.new_baseline_id.bac, 33700.0, places=2)

    def test_prj03_change_request_needs_an_approved_baseline(self):
        # PRJ-03: a change is measured against an approved reference, a draft is simply edited
        cr = self._cr(self._baseline(approve=False))
        with self.assertRaises(UserError):
            cr.action_approve()
        self.assertEqual(cr.state, "draft")

    def test_prj03_change_request_approved_once(self):
        # PRJ-03: a second approval would create a second re-baseline of the same change
        cr = self._cr(self._baseline())
        cr.action_approve()
        with self.assertRaises(UserError):
            cr.action_approve()

    def test_prj03_rejected_change_request_creates_nothing(self):
        # PRJ-03: a rejected change stays on record, without a new baseline
        baseline = self._baseline()
        cr = self._cr(baseline)
        cr.action_reject()
        self.assertEqual(cr.state, "rejected")
        self.assertFalse(cr.new_baseline_id)
        self.assertFalse(self.env["cmc.plan.version"].search([("parent_id", "=", baseline.id)]))
        with self.assertRaises(UserError):
            cr.action_approve()

    def test_prj03_change_request_inside_the_project_and_span(self):
        # PRJ-03: the change falls on a task of the baseline project and inside its span
        baseline = self._baseline()
        with self.assertRaises(ValidationError):
            self._cr(baseline, task_id=self.tq.id)
        with self.assertRaises(ValidationError):
            self._cr(baseline, date=date(2026, 8, 1))

    # ---- RN-001: change requests accumulate on the lineage tip -------------------------------------------

    def _two_crs(self, baseline):
        cr_build = self._cr(baseline)
        cr_test = self._cr(baseline, name="CR-02 extra test bench", task_id=self.t3.id, cost_impact=800.0)
        return cr_build, cr_test

    def test_rn001_change_requests_accumulate(self):
        # RN-001: two CRs on the same baseline approved in sequence -> the second copies the first's re-baseline
        baseline = self._baseline()
        cr_build, cr_test = self._two_crs(baseline)
        cr_build.action_approve()
        cr_test.action_approve()
        first, final = cr_build.new_baseline_id, cr_test.new_baseline_id
        self.assertRecordValues(final, [{"parent_id": first.id, "state": "draft", "bac": 34500.0}])
        self.assertEqual(len(final.estimate_line_ids), 5)
        self.assertEqual(
            set(final.estimate_line_ids.mapped("name")), {"Design", "Build", "Test", "CR-01 extra welding", "CR-02 extra test bench"},
        )
        april = final.baseline_line_ids.filtered(lambda r: r.date == APR)
        self.assertAlmostEqual(sum(april.filtered(lambda r: r.task_id == self.t2).mapped("planned_value")), 9200.0, places=2)
        self.assertAlmostEqual(sum(april.filtered(lambda r: r.task_id == self.t3).mapped("planned_value")), 2725.0, places=2)
        # the copied versions are untouched
        self.assertAlmostEqual(first.bac, 33700.0, places=2)
        self.assertAlmostEqual(baseline.bac, 32200.0, places=2)
        final.action_approve()
        self.assertRecordValues(final, [{"state": "done", "bac": 34500.0}])

    def test_rn001_copy_from_the_approved_child(self):
        # RN-001: the CR baseline already has an approved re-baseline -> the copy is made from that child
        baseline = self._baseline()
        cr_build, cr_test = self._two_crs(baseline)
        cr_build.action_approve()
        child = cr_build.new_baseline_id
        child.action_approve()
        self.assertEqual(child.state, "done")
        cr_test.action_approve()
        self.assertRecordValues(cr_test.new_baseline_id, [{"parent_id": child.id, "bac": 34500.0}])
        self.assertIn("CR-01 extra welding", cr_test.new_baseline_id.estimate_line_ids.mapped("name"))

    def test_rn001_superseded_baseline_cannot_be_approved(self):
        # RN-001: a baseline with a descendant is no longer the reference: approving it would fork the lineage
        baseline = self._baseline()
        cr_build, cr_test = self._two_crs(baseline)
        cr_build.action_approve()
        cr_test.action_approve()
        superseded = cr_build.new_baseline_id
        with self.assertRaises(UserError):
            superseded.action_approve()
        self.assertEqual(superseded.state, "draft")

    # ---- RN-002: descope ----------------------------------------------------------------------------------

    def test_rn002_negative_change_request_descopes(self):
        # RN-002: a negative cost impact is a scope reduction, never a silent zero: BAC 32200 - 1500 = 30700
        cr = self._cr(self._baseline(), name="CR-03 drop coating", cost_impact=-1500.0)
        cr.action_approve()
        new = cr.new_baseline_id
        added = new.estimate_line_ids.filtered(lambda line: line.name == "CR-03 drop coating")
        self.assertRecordValues(added, [{"material_cost": -1500.0, "cost_expected": -1500.0, "cost_sigma": 0.0}])
        self.assertAlmostEqual(new.bac, 30700.0, places=2)
        april_t2 = new.baseline_line_ids.filtered(lambda r: r.task_id == self.t2 and r.date == APR)
        self.assertAlmostEqual(sum(april_t2.mapped("planned_value")), 6200.0, places=2)
        self.assertAlmostEqual(new.estimate_expected, 30700.0, places=2)
