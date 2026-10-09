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
