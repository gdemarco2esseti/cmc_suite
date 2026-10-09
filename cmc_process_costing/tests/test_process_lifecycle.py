"""WP-06 / AD-113 / AD-002 - run lifecycle, input validation, dependencies between runs.

CONTRACT (part, see common.py)
- Period = exactly one calendar month; percentages 0..100; units >= 0 (ValidationError).
- ``previous_run_id``: same department and product, earlier month; ``previous_dept_run_id``: another department,
  same period (ValidationError).
- Computed run locked (UserError on input write); reset clears outputs and lines.
- Compute records the usage of the closing, the previous month run and the upstream department run: none of them
  can be reset while this run is computed (UserError); a draft source -> UserError at Compute.
- RN-002: the closing period must equal the run period (a quarter closing on a monthly run -> UserError).
- RN-001: a computed run accepts no write on any stored field outside ``_cmc_editable_when_done`` (UserError), see
  ``test_process_run_lock.py``.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import N_FROM, N_TO, P_FROM, P_TO, ProcessCase


@tagged("post_install", "-at_install")
class TestProcessLifecycle(ProcessCase):
    def test_period_must_be_one_month(self):
        # AD-105: a process run is one department x one calendar month
        with self.assertRaises(ValidationError):
            self._run(date_to=N_TO, prefill=False, compute=False)
        with self.assertRaises(ValidationError):
            self._run(date_from=P_FROM + timedelta(days=1), prefill=False, compute=False)

    def test_percent_out_of_range(self):
        with self.assertRaises(ValidationError):
            self._run(prefill=False, compute=False, ending_pct_conversion=120.0)
        with self.assertRaises(ValidationError):
            self._run(prefill=False, compute=False, beginning_pct_materials=-5.0)

    def test_negative_units_refused(self):
        with self.assertRaises(ValidationError):
            self._run(prefill=False, compute=False, ending_units=-1.0)

    def test_previous_run_other_department_refused(self):
        # FC-07: the beginning WIP of Assembly cannot come from Finishing
        other = self._run(center=self.d2, product=self.f, typed={}, prefill=False, compute=False)
        with self.assertRaises(ValidationError):
            self._run(previous_run_id=other.id, date_from=N_FROM, date_to=N_TO, prefill=False, compute=False)

    def test_previous_run_must_be_earlier(self):
        same = self._d1(compute=False)
        with self.assertRaises(ValidationError):
            self._run(previous_run_id=same.id, prefill=False, compute=False)

    def test_upstream_run_same_period(self):
        # FC-07: transferred-in comes from the upstream department of the SAME month
        upstream = self._run(date_from=N_FROM, date_to=N_TO, prefill=False, compute=False)
        with self.assertRaises(ValidationError):
            self._d2(upstream, prefill=False, compute=False)
        with self.assertRaises(ValidationError):
            self._run(previous_dept_run_id=self._d1(compute=False).id, prefill=False, compute=False)

    def test_computed_run_locked_and_reset_clears(self):
        # AD-001: WIP % typed on the draft, locked once computed; reset deletes the results
        run = self._d1()
        with self.assertRaises(UserError):
            run.ending_pct_conversion = 40.0
        run.action_reset_to_draft()
        self.assertRecordValues(run, [{"state": "draft", "cost_to_account": 0.0, "transferred_out_cost": 0.0,
                                       "ending_wip_cost": 0.0, "abnormal_loss": 0.0}])
        self.assertFalse(run.line_ids)
        run.ending_pct_conversion = 40.0
        run.action_compute()
        self.assertAlmostEqual(self._line(run, "conversion").equivalent_units, 1080.0)

    def test_prefill_only_on_draft(self):
        run = self._d1()
        with self.assertRaises(UserError):
            run.action_cmc_prefill()

    def test_upstream_reset_blocked_by_chained_run(self):
        # AD-002: Finishing read the transferred-out cost of Assembly
        d1 = self._d1()
        self._d2(d1)
        with self.assertRaises(UserError):
            d1.action_reset_to_draft()

    def test_previous_month_reset_blocked(self):
        prev = self._d1()
        nxt = self._run(typed={"ending_units": 0.0}, previous_run_id=prev.id, date_from=N_FROM, date_to=N_TO,
                        compute=False)
        nxt.write({"units_started": 0.0, "units_completed": 200.0})
        nxt.action_compute()
        with self.assertRaises(UserError):
            prev.action_reset_to_draft()

    def test_closing_reset_blocked(self):
        self._d1()
        with self.assertRaises(UserError):
            self.closing.action_reset_to_draft()

    def test_draft_upstream_refused(self):
        d1 = self._d1(compute=False)
        d2 = self._run(center=self.d2, product=self.f, typed=dict(units_started=900.0, units_completed=800.0, ending_units=100.0,
                       ending_pct_conversion=50.0), previous_dept_run_id=d1.id, prefill=False, compute=False)
        with self.assertRaises(UserError):
            d2.action_compute()

    def test_draft_closing_refused(self):
        closing = self.env["cmc.closing.run"].create(
            {"name": "WP06 draft closing", "method": "direct", "company_id": self.company.id,
             "date_from": P_FROM, "date_to": P_TO}
        )
        run = self._run(closing_run_id=closing.id, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_closing_must_cover_period(self):
        closing = self.env["cmc.closing.run"].create(
            {"name": "WP06 next closing", "method": "direct", "company_id": self.company.id,
             "date_from": N_FROM, "date_to": N_TO}
        )
        closing.action_compute()
        run = self._run(closing_run_id=closing.id, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_closing_period_must_equal_run_period(self):
        # RN-002: a closing wider than the month (a quarter around P) reallocates the services of the other months
        # into this run's conversion cost: the closing period must be exactly the run period
        closing = self.env["cmc.closing.run"].create(
            {"name": "WP06 quarter closing", "method": "direct", "company_id": self.company.id,
             "date_from": P_FROM - relativedelta(months=1), "date_to": N_TO}
        )
        closing.action_compute()
        run = self._run(closing_run_id=closing.id, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")
