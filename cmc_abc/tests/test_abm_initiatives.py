"""WP-08 / ABM-04 - improvement initiatives: business case (saving, one-time cost, payback) and realised benefits.

CONTRACT (see abm_common)
- ``cmc.abm.initiative``: activity_id (activity node), baseline_run_id / review_run_id (done, different),
  expected_driver_reduction, baseline_annual_cost, expected_annual_saving (stored compute, editable), one_time_cost,
  payback_years / payback_months, realized_annual_saving, realization_percent, freed_capacity_qty / _cost,
  resource_action, benefit_status pending / not_realized / partial / realized.
- AD-119 pure helper ``odoo.addons.cmc_base.models.cmc_utils.simple_payback(one_time_cost, annual_saving)``.
Hand figures: baseline March QC total 2500 (1 month) -> annual 30000; reduction 40 % -> saving 12000;
one-time 6000 -> payback 0.5 y = 6 months. Kaplan Ch.8: 583300 / 808291 = 0.72165 y.
Review run (R1 SETUP 30 / MACH 58 / QC 12, QC inspections P1 2 / P2 1): QC 1200 + tag 500 = 1700, used 3 ->
realised (2500 - 1700) x 12 = 9600 = 80 %, freed 2 inspections x baseline rate 500 = 1000 -> partial.
Frost St.8: usage down (freed 2) but same spending 2500 -> realised 0 -> not_realized.
RN-002: a run referenced as baseline or review of an initiative cannot be reset (UserError, dependents check:
the initiative figures would silently become 0). A TYPED ``expected_annual_saving`` is never overwritten by a
later recompute (reduction, baseline change): the reduction only DEFAULTS the saving while it is empty (0).
"""

from odoo.addons.cmc_base.models import cmc_utils
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .abm_common import AbmCase


@tagged("post_install", "-at_install")
class TestAbmInitiatives(AbmCase):
    def setUp(self):
        super().setUp()
        self.baseline = self._main_run()

    def _initiative(self, **vals):
        return self.env_a["cmc.abm.initiative"].create({
            "name": "Automate inspection", "activity_id": self.qc.id, "project_type": "reduce",
            "baseline_run_id": self.baseline.id, "expected_driver_reduction": 40.0, "one_time_cost": 6000.0, **vals,
        })

    def _review(self, qc_percent=12, mach_percent=58, qc_p1=2, qc_p2=1):
        return self._main_run(
            name="WP08 review", resource_lines=self._lines_with(mach=mach_percent, qc=qc_percent), edges=self._edges_qc(qc_p1, qc_p2)
        )

    def test_abm04_simple_payback_helper(self):
        # ABM-04 / AD-119 / Kaplan Ch.8: payback = investment / annual saving; no saving -> 0 (not computable)
        self.assertAlmostEqual(cmc_utils.simple_payback(583300.0, 808291.0), 0.72165, places=4)
        self.assertAlmostEqual(cmc_utils.simple_payback(6000.0, 12000.0), 0.5, places=6)
        self.assertEqual(cmc_utils.simple_payback(6000.0, 0.0), 0.0)
        self.assertEqual(cmc_utils.simple_payback(6000.0, -10.0), 0.0)

    def test_abm04_business_case_from_the_baseline_run(self):
        # ABM-04: annual activity cost x expected reduction = saving; payback = one-time / saving
        initiative = self._initiative()
        self.assertRecordValues(initiative, [{
            "company_id": self.company_a.id, "baseline_annual_cost": 30000.0, "expected_annual_saving": 12000.0,
            "payback_years": 0.5, "payback_months": 6.0, "benefit_status": "pending", "realized_annual_saving": 0.0,
        }])

    def test_abm04_saving_can_be_typed(self):
        # ABM-04 / Kaplan Ch.8: the expected saving can come from an outside estimate (808291 on 583300)
        initiative = self._initiative(one_time_cost=583300.0)
        initiative.expected_annual_saving = 808291.0
        self.assertAlmostEqual(initiative.payback_years, 0.72165, places=4)

    def test_abm04_no_saving_no_payback(self):
        # ABM-04: zero expected saving -> payback 0, never a ZeroDivisionError
        initiative = self._initiative(expected_driver_reduction=0.0)
        self.assertEqual(initiative.expected_annual_saving, 0.0)
        self.assertEqual(initiative.payback_years, 0.0)

    def test_abm04_realised_saving_and_freed_capacity(self):
        # ABM-04 / Kaplan Ch.8: repeat the ABC model to verify the realised benefit
        initiative = self._initiative(review_run_id=self._review().id, resource_action="redeploy")
        self.assertRecordValues(initiative, [{
            "realized_annual_saving": 9600.0, "realization_percent": 80.0, "freed_capacity_qty": 2.0,
            "freed_capacity_cost": 1000.0, "benefit_status": "partial",
        }])

    def test_abm04_freed_capacity_is_not_a_saving(self):
        # ABM-04 / Frost St.8: lower usage with unchanged spending only moves cost to idle capacity
        initiative = self._initiative(review_run_id=self._review(qc_percent=20, mach_percent=50).id)
        self.assertRecordValues(initiative, [{
            "realized_annual_saving": 0.0, "freed_capacity_qty": 2.0, "freed_capacity_cost": 1000.0,
            "benefit_status": "not_realized",
        }])

    def test_abm04_fully_realised(self):
        # ABM-04: realised >= expected -> realized (QC 1000 + 500 = 1500 -> 12000 = 100 %)
        initiative = self._initiative(review_run_id=self._review(qc_percent=10, mach_percent=60).id)
        self.assertAlmostEqual(initiative.realization_percent, 100.0, places=2)
        self.assertEqual(initiative.benefit_status, "realized")

    def test_abm04_target_must_be_an_activity(self):
        # ABM-04: an initiative targets an activity (the unit that carries cost and driver)
        with self.assertRaises(ValidationError):
            self._initiative(activity_id=self.pr_mfg.id)

    def test_abm04_runs_must_be_computed(self):
        # ABM-04: baseline and review are certified figures, not drafts
        draft = self._main_run(compute=False)
        with self.assertRaises(ValidationError):
            self._initiative(baseline_run_id=draft.id)
        with self.assertRaises(ValidationError):
            self._initiative(review_run_id=draft.id)

    def test_abm04_review_differs_from_baseline(self):
        # ABM-04: a review equal to the baseline proves nothing
        with self.assertRaises(ValidationError):
            self._initiative(review_run_id=self.baseline.id)

    def test_abm04_run_of_another_company(self):
        # ABM-04 / multi-company: the baseline run belongs to the initiative company
        company_b = self.env["res.company"].create({"name": "CMC WP08 init B"})
        with self.assertRaises(UserError):
            self.env["cmc.abm.initiative"].create({
                "name": "cross", "company_id": company_b.id, "activity_id": self.qc.id,
                "baseline_run_id": self.baseline.id,
            })

    # ---- RN-002: initiatives depend on their runs; typed savings are kept ---------

    def test_rn002_reset_of_baseline_run_refused(self):
        # RN-002 / AD-002: resetting the baseline would zero the business case of the initiative
        self._initiative()
        with self.assertRaises(UserError):
            self.baseline.action_reset_to_draft()

    def test_rn002_reset_of_review_run_refused(self):
        # RN-002 / AD-002: resetting the review would turn a verified benefit back into "pending"-like zeros
        review = self._review()
        self._initiative(review_run_id=review.id)
        with self.assertRaises(UserError):
            review.action_reset_to_draft()

    def test_rn002_reset_refused_for_the_cmc_manager_too(self):
        # RN-002: the dependents check is not an ACL: the manager who may reset runs is stopped as well
        manager = new_test_user(self.env, login="wp08_init_manager", groups="base.group_user,cmc_base.group_cmc_manager",
                                company_id=self.company_a.id, company_ids=[(6, 0, self.company_a.ids)])
        self._initiative()
        with self.assertRaises(UserError):
            self.baseline.with_user(manager).with_context(allowed_company_ids=self.company_a.ids).action_reset_to_draft()

    def test_rn002_reset_allowed_once_the_initiative_is_gone(self):
        # RN-002: without dependents the run resets as before
        self._initiative().unlink()
        self.baseline.action_reset_to_draft()
        self.assertEqual(self.baseline.state, "draft")

    def test_rn002_typed_saving_survives_a_reduction_change(self):
        # RN-002: an outside estimate typed by the team is not replaced when the reduction is edited
        initiative = self._initiative(one_time_cost=583300.0)
        initiative.expected_annual_saving = 808291.0
        initiative.expected_driver_reduction = 50.0
        self.assertEqual(initiative.expected_annual_saving, 808291.0)
        self.assertAlmostEqual(initiative.payback_years, 0.72165, places=4)

    def test_rn002_typed_saving_survives_a_baseline_change(self):
        # RN-002: changing the baseline run recomputes the annual cost, never the typed saving
        initiative = self._initiative()
        initiative.expected_annual_saving = 5000.0
        april = self._april_run()
        initiative.baseline_run_id = april
        self.assertAlmostEqual(initiative.baseline_annual_cost, 9999.0 * 12, places=2)  # April QC total 9999
        self.assertEqual(initiative.expected_annual_saving, 5000.0)

    def test_rn002_saving_typed_at_creation_is_kept(self):
        # RN-002: a saving given at creation wins over the reduction default
        initiative = self._initiative(expected_annual_saving=5000.0)
        initiative.expected_driver_reduction = 60.0
        self.assertEqual(initiative.expected_annual_saving, 5000.0)

    def test_rn002_empty_saving_is_defaulted_from_the_reduction(self):
        # RN-002: while the saving is empty (0) the reduction keeps defaulting it
        initiative = self._initiative(expected_driver_reduction=0.0)
        self.assertEqual(initiative.expected_annual_saving, 0.0)
        initiative.expected_driver_reduction = 40.0
        self.assertAlmostEqual(initiative.expected_annual_saving, 12000.0, places=2)
