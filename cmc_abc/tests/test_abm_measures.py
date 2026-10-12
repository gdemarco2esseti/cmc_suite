"""WP-08 / ABM-03 - performance measures per activity (volume, cost per output, cycle time, quality) and trend.

CONTRACT (see abm_common)
- ``cmc.abc.activity.measure_line_ids`` -> ``cmc.abc.activity.measure`` (activity_id, measure_id statistical, role
  output / cycle_time / quality, target). ValidationError: allocation driver, non-activity node, duplicate role.
- ``cmc.measure.quantity.activity_id``: activity grain of the facts (actual, run company and span).
- Run results ``performance_ids`` -> ``cmc.abc.activity.performance`` (activity_id, month, activity_cost = total_cost,
  output_qty, cost_per_output, cycle_time, defect_qty, quality_rate %); no output line -> output = used_qty.
- ``activity._cmc_performance_trend(date_from, date_to)``: month figure rule (latest done run of the month).
Hand figures: March QC total 2500, output 10 inspections done, 15 h, 1 escape -> 250 / 1.5 h / 10 %;
SETUP total 3650 (3400 + ADMIN 250), no output line -> used 10 setups, 20 h -> 365 / 2.0 h / 0 %.
April QC 9999, output 11, 22 h, no escape -> 909 / 2.0 h / 0 %. A March fact of the same measure on a cost
object (500) and a planned fact are not activity facts and stay out.
"""

from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .abm_common import APR_TO, AbmCase
from .abc2_common import D_FROM, D_MID


@tagged("post_install", "-at_install")
class TestAbmMeasures(AbmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        cls.insp_done = cls._driver(env, "WP08IDONE", "transaction", kind="statistical")
        cls.insp_hours = cls._driver(env, "WP08IHRS", "duration", kind="statistical")
        cls.escapes = cls._driver(env, "WP08ESC", "transaction", kind="statistical")
        cls.setup_hours = cls._driver(env, "WP08SHRS", "duration", kind="statistical")
        Line = env["cmc.abc.activity.measure"]
        Line.create([
            {"activity_id": cls.qc.id, "measure_id": cls.insp_done.id, "role": "output", "target": 12},
            {"activity_id": cls.qc.id, "measure_id": cls.insp_hours.id, "role": "cycle_time"},
            {"activity_id": cls.qc.id, "measure_id": cls.escapes.id, "role": "quality"},
            {"activity_id": cls.setup.id, "measure_id": cls.setup_hours.id, "role": "cycle_time"},
        ])
        Fact = env["cmc.measure.quantity"]
        common = {"company_id": cls.company_a.id}
        Fact.create([
            {"driver_id": cls.insp_done.id, "activity_id": cls.qc.id, "date": D_MID, "quantity": 10, **common},
            {"driver_id": cls.insp_hours.id, "activity_id": cls.qc.id, "date": D_MID, "quantity": 15, **common},
            {"driver_id": cls.escapes.id, "activity_id": cls.qc.id, "date": D_MID, "quantity": 1, **common},
            {"driver_id": cls.setup_hours.id, "activity_id": cls.setup.id, "date": D_MID, "quantity": 20, **common},
            # not an activity fact: same measure on a cost object (other grain)
            {"driver_id": cls.insp_done.id, "cost_object_id": cls.obj_p1.id, "date": D_MID, "quantity": 500, **common},
            {"driver_id": cls.insp_done.id, "activity_id": cls.qc.id, "date": date(2027, 4, 10), "quantity": 11, **common},
            {"driver_id": cls.insp_hours.id, "activity_id": cls.qc.id, "date": date(2027, 4, 10), "quantity": 22, **common},
        ])

    def _perf(self, run, activity):
        return run.performance_ids.filtered(lambda p: p.activity_id == activity)

    def test_abm03_cost_per_output_cycle_time_quality(self):
        # ABM-03 / Turney Ch.4: cost per output = pool / volume, cycle time and quality per output
        run = self._main_run()
        self.assertRecordValues(self._perf(run, self.qc), [{
            "month": D_FROM, "activity_cost": 2500.0, "output_qty": 10.0, "cost_per_output": 250.0,
            "cycle_time": 1.5, "defect_qty": 1.0, "quality_rate": 10.0,
        }])

    def test_abm03_output_defaults_to_driver_volume(self):
        # ABM-03: without an output measure the volume is the activity driver quantity actually used
        run = self._main_run()
        self.assertRecordValues(self._perf(run, self.setup), [{
            "activity_cost": 3650.0, "output_qty": 10.0, "cost_per_output": 365.0,
            "cycle_time": 2.0, "defect_qty": 0.0, "quality_rate": 0.0,
        }])

    def test_abm03_only_measured_activities_get_rows(self):
        # ABM-03: performance rows exist for the activities that declare measures
        run = self._main_run()
        self.assertEqual(run.performance_ids.activity_id, self.qc | self.setup)

    def test_abm03_zero_output_never_divides_by_zero(self):
        # ABM-03: an output measure with no fact gives 0 per-output figures, not a crash
        mach_out = self._driver(self.env_a, "WP08MOUT", "transaction", kind="statistical")
        self.env_a["cmc.abc.activity.measure"].create({"activity_id": self.mach.id, "measure_id": mach_out.id, "role": "output"})
        run = self._main_run()
        self.assertRecordValues(self._perf(run, self.mach), [{"output_qty": 0.0, "cost_per_output": 0.0, "cycle_time": 0.0}])

    def test_abm03_planned_facts_do_not_count(self):
        # ABM-03 / AD-104: a planned (version) fact is not an actual measure
        version = self.env_a["cmc.plan.version"].create({
            "name": "WP08 plan", "company_id": self.company_a.id, "date_from": D_FROM, "date_to": date(2027, 12, 31),
        })
        self.env_a["cmc.measure.quantity"].create({
            "driver_id": self.insp_done.id, "activity_id": self.qc.id, "date": D_MID, "quantity": 90,
            "company_id": self.company_a.id, "version_id": version.id,
        })
        run = self._main_run()
        self.assertAlmostEqual(self._perf(run, self.qc).output_qty, 10.0, places=2)

    def test_abm03_measure_must_be_statistical(self):
        # ABM-03 / AD-107: an allocation driver is not a performance measure
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abc.activity.measure"].create({"activity_id": self.mach.id, "measure_id": self.mhours.id, "role": "output"})

    def test_abm03_measure_only_on_activities(self):
        # ABM-03: a process has no cost of its own to measure per output
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abc.activity.measure"].create({"activity_id": self.pr_mfg.id, "measure_id": self.escapes.id, "role": "quality"})

    def test_abm03_one_measure_per_role(self):
        # ABM-03: two output measures would make cost per output ambiguous
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abc.activity.measure"].create({"activity_id": self.qc.id, "measure_id": self.escapes.id, "role": "output"})

    def test_abm03_month_trend(self):
        # ABM-03 / Turney Ch.4: driver vs cost trend per month
        self._main_run()
        self._april_run()
        trend = self.qc._cmc_performance_trend(D_FROM, APR_TO)
        self.assertEqual([row["month"] for row in trend], [D_FROM, date(2027, 4, 1)])
        march, april = trend
        self.assertAlmostEqual(march["activity_cost"], 2500.0, places=2)
        self.assertAlmostEqual(march["cost_per_output"], 250.0, places=2)
        self.assertAlmostEqual(march["quality_rate"], 10.0, places=2)
        self.assertAlmostEqual(april["activity_cost"], 9999.0, places=2)
        self.assertAlmostEqual(april["output_qty"], 11.0, places=2)
        self.assertAlmostEqual(april["cost_per_output"], 909.0, places=2)
        self.assertAlmostEqual(april["cycle_time"], 2.0, places=2)
        self.assertAlmostEqual(april["quality_rate"], 0.0, places=2)

    def test_abm03_trend_takes_latest_done_run_of_the_month(self):
        # ABM-03: a recomputed month replaces the earlier one; a draft run never counts
        self._main_run()
        self._main_run(resource_lines=self._lines_with(mach=58, qc=12))  # QC 1200 + tag 500 = 1700
        draft = self._april_run(compute=False)
        trend = self.qc._cmc_performance_trend(D_FROM, APR_TO)
        self.assertEqual(len(trend), 1)
        self.assertAlmostEqual(trend[0]["activity_cost"], 1700.0, places=2)
        self.assertAlmostEqual(trend[0]["cost_per_output"], 170.0, places=2)
        draft.action_compute()
        draft.action_reset_to_draft()
        self.assertEqual(len(self.qc._cmc_performance_trend(D_FROM, APR_TO)), 1)

    def test_abm03_reset_removes_performance_rows(self):
        # ABM-03: performance rows are run results
        run = self._main_run()
        self.assertTrue(run.performance_ids)
        run.action_reset_to_draft()
        self.assertFalse(run.performance_ids)
