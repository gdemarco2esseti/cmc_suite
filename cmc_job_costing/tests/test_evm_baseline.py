"""WP-15 / PRJ-02 - time-phased cost baseline by task and month, risk register (EMV), reserves, BAC.

CONTRACT: see ``evm_common`` (baseline version, ``cmc.evm.baseline.line``, ``cmc.evm.risk``, reserves).
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .evm_common import APR, FEB, JAN, MAR, MAY, EvmCase


@tagged("post_install", "-at_install")
class TestEvmBaseline(EvmCase):
    def test_prj02_approve_builds_time_phased_baseline(self):
        # PRJ-02 / AD-105: approval phases each task's expected cost evenly over its months (PV per task per month)
        version = self._baseline()
        self.assertEqual(version.state, "done")
        rows = version.baseline_line_ids.sorted(lambda r: (r.task_id.id, r.date))
        self.assertRecordValues(rows, [
            {"task_id": self.t1.id, "date": JAN, "planned_value": 2625.0},
            {"task_id": self.t1.id, "date": FEB, "planned_value": 2625.0},
            {"task_id": self.t2.id, "date": FEB, "planned_value": 7700.0},
            {"task_id": self.t2.id, "date": MAR, "planned_value": 7700.0},
            {"task_id": self.t2.id, "date": APR, "planned_value": 7700.0},
            {"task_id": self.t3.id, "date": APR, "planned_value": 1925.0},
            {"task_id": self.t3.id, "date": MAY, "planned_value": 1925.0},
        ])
        self.assertAlmostEqual(version.bac, 32200.0, places=2)

    def test_prj02_phasing_conserves_the_task_total(self):
        # PRJ-02: 100.00 over three months re-sums to 100.00 (no cent lost or created)
        version = self._baseline(risks=False, rate_card=(), estimates=[
            {"name": "Permits", "task_id": self.t1.id, "hours_optimistic": 0, "hours_most_likely": 0, "hours_pessimistic": 0,
             "material_cost": 100.0, "date_start": JAN, "date_end": date(2026, 3, 31)},
        ])
        values = version.baseline_line_ids.mapped("planned_value")
        self.assertEqual(len(values), 3)
        self.assertAlmostEqual(sum(values), 100.0, places=2)
        self.assertTrue(all(round(v, 2) in (33.33, 33.34) for v in values), values)

    def test_prj02_two_lines_of_a_task_in_the_same_month_are_summed(self):
        # PRJ-02: the baseline grain is task x month, not estimate line x month
        estimates = self._estimate_vals()[:1]
        estimates.append({"name": "Design licence", "task_id": self.t1.id, "hours_optimistic": 0, "hours_most_likely": 0,
                          "hours_pessimistic": 0, "material_cost": 400.0, "date_start": FEB, "date_end": date(2026, 2, 28)})
        version = self._baseline(risks=False, estimates=estimates)
        self.assertRecordValues(version.baseline_line_ids.sorted("date"), [
            {"task_id": self.t1.id, "date": JAN, "planned_value": 2625.0},
            {"task_id": self.t1.id, "date": FEB, "planned_value": 3025.0},
        ])

    def test_prj02_plan_replaces_the_baseline_lines(self):
        # PRJ-02: re-planning a draft never appends a second copy of the PV
        version = self._baseline(approve=False)
        version.action_cmc_evm_plan()
        version.action_cmc_evm_plan()
        self.assertEqual(len(version.baseline_line_ids), 7)
        self.assertAlmostEqual(version.bac, 32200.0, places=2)

    def test_prj02_risk_register_emv_and_ranking(self):
        # PRJ-02: EMV = P x I, opportunities negative; the register is ranked by EMV
        version = self._baseline()
        self.assertRecordValues(version.risk_ids, [
            {"name": "Supplier delay", "emv": 2000.0},
            {"name": "Rework", "emv": 1000.0},
            {"name": "Cheaper steel", "emv": -600.0},
        ])

    def test_prj02_reserves_and_project_budget(self):
        # PRJ-02 (PCM Ch7): CR = sum EMV; cost baseline = BAC + CR; MR % outside the baseline; budget = baseline + MR
        version = self._baseline()
        self.assertRecordValues(version, [{
            "bac": 32200.0, "contingency_reserve": 2400.0, "cost_baseline": 34600.0,
            "management_reserve": 3460.0, "project_budget": 38060.0,
        }])

    def test_prj02_probability_is_a_percent(self):
        # PRJ-02: a probability lies in 0..100 %
        version = self._baseline(approve=False, risks=False)
        for probability in (120.0, -5.0):
            with self.subTest(probability=probability), self.assertRaises(ValidationError):
                self.env["cmc.evm.risk"].create({"version_id": version.id, "name": "Bad", "probability": probability, "impact": 100.0})

    def test_prj02_baseline_needs_a_project(self):
        # PRJ-02: a baseline is the plan of ONE project
        with self.assertRaises(ValidationError):
            self._baseline(approve=False, project_id=False, estimates=[], risks=False, rate_card=())

    def test_prj02_approve_without_estimate_refused(self):
        # PRJ-02: an empty baseline would be BAC 0 and every index meaningless
        version = self._baseline(approve=False, estimates=[], risks=False, rate_card=())
        with self.assertRaises(UserError):
            version.action_approve()
        self.assertEqual(version.state, "draft")

    def test_prj02_approved_baseline_is_locked(self):
        # PRJ-02 / AD-104: the approved baseline is the reference: no estimate, risk or plan change
        version = self._baseline()
        with self.assertRaises(UserError):
            version.estimate_line_ids[:1].material_cost = 999.0
        with self.assertRaises(UserError):
            self.env["cmc.evm.risk"].create({"version_id": version.id, "name": "Late", "probability": 10.0, "impact": 100.0})
        with self.assertRaises(UserError):
            version.risk_ids[:1].unlink()
        with self.assertRaises(UserError):
            version.action_cmc_evm_plan()

    def test_prj02_reset_and_reapprove_is_idempotent(self):
        # PRJ-02: reopening (no EVM run reads it) and approving again yields the same baseline
        version = self._baseline()
        version.action_reset_to_draft()
        version.action_approve()
        self.assertEqual(len(version.baseline_line_ids), 7)
        self.assertAlmostEqual(version.bac, 32200.0, places=2)

    def test_prj02_other_version_types_need_no_project(self):
        # AD-104: the project rule is the baseline's own; a budget version is unaffected
        budget = self.env["cmc.plan.version"].create(
            {"name": "WP15 budget", "company_id": self.company_a.id, "date_from": JAN, "date_to": date(2026, 12, 31)}
        )
        self.assertEqual(budget.version_type, "budget")
        self.assertFalse(budget.project_id)
