"""KPI-01 measurement run: targets per version, direction, thresholds -> traffic light, score, trend; run lifecycle.

Hand numbers: yield (good / (good + scrap) x 100) Jan 95.0, Feb 90.0, Mar 98.0; version targets 96 each month
(static target 90 ignored), bands amber 0 / red 5:
  Jan gap (95 - 96) / 96 = -1.0417 % -> amber, score 98.9583; Feb -6.25 % -> red, 93.75; Mar +2.0833 % -> green, 102.0833.
  trend Feb -5.0 worsening, Mar +8.0 improving, Jan none.
OTD static target 95, red 10: Jan 75 -> gap -21.0526 % red, score 78.9474; Feb 100 -> +5.2632 % green.
cycle time (lower is better) 5.0 vs 6.0 -> gap (6 - 5) / 6 = +16.6667 % green, score 116.6667.
rent BvA target 0 -> gap = the value itself: Jan +10 green (score 110), Feb -20 red (score 80).
RN-003: cascade "ops_excellence" (yield 60 / otd 40, static targets) Jan: yield 95 vs 90 -> score 105.5556,
  otd 78.9474 -> 0.6 x 105.5556 + 0.4 x 78.9474 = 94.9123.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import DEC_2025, FEB, JAN, MAR, KpiCase


@tagged("post_install", "-at_install")
class TestKpiRun(KpiCase):
    def _yield_run(self, **vals):
        lib = self._library()
        version = self._target_version([(lib["yield_pct"], month, 96.0) for month in (JAN, FEB, MAR)])
        return lib, self._run(JAN, MAR, version_id=version.id, **vals)

    def test_every_active_kpi_every_month(self):
        # KPI-01: one row per active KPI x month of the span; an archived KPI (read by no formula) is skipped
        lib = self._library()
        lib["cycle_time"].active = False
        run = self._run(JAN, MAR)
        self.assertEqual(len(run.value_ids), (len(lib) - 1) * 3)
        self.assertFalse(run.value_ids.filtered(lambda r: r.kpi_id == lib["cycle_time"]))
        self.assertEqual(set(run.value_ids.mapped("date")), {JAN, FEB, MAR})

    def test_selected_kpis_only(self):
        # KPI-01: a run restricted to some KPIs computes those (and nothing else)
        lib = self._library()
        run = self._run(JAN, kpi_ids=[(6, 0, (lib["otd"] | lib["cycle_time"]).ids)])
        self.assertEqual(run.value_ids.kpi_id, lib["otd"] | lib["cycle_time"])

    def test_dates_ordered(self):
        # AD-105: the span runs forward
        with self.assertRaises(ValidationError):
            self._run(FEB, JAN, compute=False)

    def test_target_from_version_else_static(self):
        # KPI-01: the version target of the month wins; without one the static target applies
        lib, run = self._yield_run()
        self.assertRecordValues(self._val(run, lib["yield_pct"], JAN), [{"target": 96.0, "target_source": "version"}])
        self.assertRecordValues(self._val(run, lib["otd"], JAN), [{"target": 95.0, "target_source": "static"}])
        self.assertRecordValues(self._val(self._run(JAN), lib["yield_pct"], JAN), [{"target": 90.0, "target_source": "static"}])

    def test_traffic_light_higher_is_better(self):
        # KPI-01: status = f(value vs target, bands): amber within 5 %, red beyond, green when met
        lib, run = self._yield_run()
        rows = [self._val(run, lib["yield_pct"], month) for month in (JAN, FEB, MAR)]
        self.assertEqual([row.status for row in rows], ["amber", "red", "green"])
        for row, value, gap, score in zip(rows, (95.0, 90.0, 98.0), (-1.041667, -6.25, 2.083333), (98.958333, 93.75, 102.083333)):
            self.assertAlmostEqual(row.value, value, places=4)
            self.assertAlmostEqual(row.gap_pct, gap, places=4)
            self.assertAlmostEqual(row.score, score, places=4)

    def test_traffic_light_lower_is_better(self):
        # KPI-01: for a lower-is-better KPI a value under target is a positive gap
        lib = self._library()
        row = self._val(self._run(JAN), lib["cycle_time"], JAN)
        self.assertEqual(row.status, "green")
        self.assertAlmostEqual(row.gap_pct, 16.666667, places=4)
        self.assertAlmostEqual(row.score, 116.666667, places=4)
        lib["cycle_time"].target_value = 4.0
        row = self._val(self._run(JAN), lib["cycle_time"], JAN)
        self.assertAlmostEqual(row.gap_pct, -25.0, places=4)
        self.assertEqual(row.status, "red")

    def test_zero_target_uses_the_difference(self):
        # KPI-01: a 0 target (variance KPIs) compares the difference itself
        lib = self._library()
        run = self._run(JAN, FEB)
        self.assertRecordValues(self._val(run, lib["rent_bva"], JAN), [{"status": "green"}])
        self.assertAlmostEqual(self._val(run, lib["rent_bva"], JAN).gap_pct, 10.0, places=4)
        self.assertAlmostEqual(self._val(run, lib["rent_bva"], JAN).score, 110.0, places=4)
        self.assertRecordValues(self._val(run, lib["rent_bva"], FEB), [{"status": "red"}])
        self.assertAlmostEqual(self._val(run, lib["rent_bva"], FEB).score, 80.0, places=4)

    def test_score_capped(self):
        # KPI-03: one runaway KPI cannot buy the scorecard: score in [0, 150]
        good = self._kpi("good_capped", "measure", driver_id=self.good.id, target_value=100.0)
        bad = self._kpi("bad_floor", "measure", driver_id=self.good.id, target_value=100.0, direction="lower")
        run = self._run(JAN)
        self.assertEqual(self._val(run, good, JAN).score, 150.0)
        self.assertEqual(self._val(run, bad, JAN).score, 0.0)

    def test_trend_within_the_run(self):
        # KPI-01: trend vs the previous month, direction-aware
        lib, run = self._yield_run()
        self.assertRecordValues(self._val(run, lib["yield_pct"], JAN), [{"trend": "none"}])
        feb, mar = self._val(run, lib["yield_pct"], FEB), self._val(run, lib["yield_pct"], MAR)
        self.assertEqual((feb.trend, mar.trend), ("worsening", "improving"))
        self.assertAlmostEqual(feb.previous_value, 95.0, places=4)
        self.assertAlmostEqual(feb.trend_delta, -5.0, places=4)
        self.assertAlmostEqual(mar.trend_delta, 8.0, places=4)

    def test_trend_lower_is_better_and_stable(self):
        # KPI-01: falling cycle time improves; an unchanged value is stable
        kpi = self._kpi("cycle_sum", "measure", driver_id=self.cycle.id, direction="lower")
        self._fact(self.cycle, FEB, 8.0)
        self._fact(self.cycle, MAR, 8.0)
        run = self._run(JAN, MAR)
        # Jan 4 + 6 = 10 -> Feb 8: less is better -> improving; Feb 8 -> Mar 8 -> stable
        self.assertEqual(self._val(run, kpi, FEB).trend, "improving")
        self.assertEqual(self._val(run, kpi, MAR).trend, "stable")

    def test_trend_from_a_previous_run(self):
        # KPI-01: the previous month computed by an earlier run feeds the trend of a new run
        lib = self._library()
        self._run(JAN)
        feb = self._val(self._run(FEB), lib["otd"], FEB)
        self.assertAlmostEqual(feb.previous_value, 75.0, places=4)
        self.assertEqual(feb.trend, "improving")

    def test_not_computable_row(self):
        # KPI-01: a KPI without data is shown as not computable (status none, no score, no trend)
        lib = self._library()
        row = self._val(self._run(MAR), lib["otd"], MAR)
        self.assertRecordValues(row, [{"computable": False, "value": 0.0, "status": "none", "score": 0.0, "trend": "none"}])

    def test_snapshot_until_reset(self):
        # AD-113: a computed run does not move with later facts; reset + compute picks them up
        kpi = self._kpi("good_qty", "measure", driver_id=self.good.id)
        run = self._run()
        self._fact(self.good, JAN, 50.0)
        self.assertEqual(self._val(run, kpi, JAN).value, 950.0)
        run.action_reset_to_draft()
        self.assertFalse(run.value_ids)
        run.action_compute()
        self.assertEqual(self._val(run, kpi, JAN).value, 1000.0)

    def test_results_written_by_compute_only(self):
        # AD-113: result rows are engine output
        kpi = self._kpi("good_qty", "measure", driver_id=self.good.id)
        row = self._val(self._run(), kpi, JAN)
        with self.assertRaises(UserError):
            row.write({"value": 1.0})

    def test_computed_run_locked(self):
        # AD-001: a computed run cannot change its inputs
        self._kpi("good_qty", "measure", driver_id=self.good.id)
        run = self._run()
        with self.assertRaises(UserError):
            run.date_to = FEB

    def test_target_lines_locked_on_approved_version(self):
        # AD-104: targets of an approved version are frozen
        lib = self._library()
        version = self._target_version([(lib["yield_pct"], JAN, 96.0)])
        version.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            version.kpi_target_ids.write({"target_value": 50.0})

    def test_target_outside_version_span(self):
        # AD-105: a target falls in the version span
        lib = self._library()
        version = self._target_version([])
        with self.assertRaises(ValidationError):
            self.env["cmc.kpi.target"].create({"version_id": version.id, "kpi_id": lib["otd"].id, "date": DEC_2025, "target_value": 1.0})

    # ---- RN-003: inputs of the selected KPIs, archived inputs, reason ----

    def test_rn003_formula_inputs_auto_included(self):
        # RN-003: selecting a formula brings in the KPIs it reads, so it is computed, not silently empty
        lib = self._library()
        run = self._run(JAN, kpi_ids=[(6, 0, lib["yield_pct"].ids)])
        self.assertEqual(run.value_ids.kpi_id, lib["yield_pct"] | lib["good_qty"] | lib["scrap_qty"])
        self.assertRecordValues(self._val(run, lib["yield_pct"], JAN), [{"computable": True}])
        self.assertAlmostEqual(self._val(run, lib["yield_pct"], JAN).value, 95.0, places=4)

    def test_rn003_cascade_children_auto_included(self):
        # RN-003: selecting a cascade brings in its children (and, transitively, what they read)
        lib = self._library()
        parent = self._kpi("ops_excellence", "cascade", perspective="internal", unit="percent")
        lib["yield_pct"].write({"parent_id": parent.id, "cascade_weight": 60.0})
        lib["otd"].write({"parent_id": parent.id, "cascade_weight": 40.0})
        run = self._run(JAN, kpi_ids=[(6, 0, parent.ids)])
        self.assertEqual(run.value_ids.kpi_id, parent | lib["yield_pct"] | lib["otd"] | lib["good_qty"] | lib["scrap_qty"])
        self.assertAlmostEqual(self._val(run, parent, JAN).value, 94.912281, places=3)

    def test_rn003_archived_input_of_active_formula_raises(self):
        # RN-003: an active formula reading an archived KPI is a configuration error, named, not a silent gap
        lib = self._library()
        lib["good_qty"].active = False
        run = self._run(JAN, compute=False)
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertIn("yield_pct", str(caught.exception))
        self.assertIn("good_qty", str(caught.exception))
        self.assertFalse(run.value_ids)

    def test_rn003_reason_of_not_computable(self):
        # RN-003: a not computable value says why, naming the KPI whose data is missing
        lib = self._library()
        late = self._kpi("late_share", "formula", formula="100 - otd", unit="percent")
        run = self._run(MAR)
        self.assertIn("otd", self._val(run, lib["otd"], MAR).reason)
        self.assertIn("otd", self._val(run, late, MAR).reason)
        self.assertFalse(self._val(run, lib["yield_pct"], MAR).reason)

    # ---- RN-002: draft sources ----

    def test_rn002_variance_on_draft_budget_raises(self):
        # RN-002: a variance against a draft budget would move under a computed run: refused
        draft = self.env["cmc.plan.version"].create(
            {"name": "KPI draft budget", "date_from": JAN, "date_to": date(2026, 12, 31), "version_type": "budget"}
        )
        self._kpi("draft_bva", "variance", variance_version_id=draft.id, center_id=self.production.id)
        with self.assertRaisesRegex(UserError, "draft_bva"):
            self._run(JAN)

    def test_rn002_draft_target_version_allowed_without_usage(self):
        # RN-002: targets may come from a draft version (documented): read, nothing recorded
        lib, run = self._yield_run()
        self.assertEqual(self._val(run, lib["yield_pct"], JAN).target_source, "version")
        self.assertNotIn(("cmc.plan.version", run.version_id.id), self._usages(run))

    def test_rn002_approved_target_version_recorded(self):
        # RN-002 / AD-002: an approved target version read by a computed run cannot be reopened under it
        lib = self._library()
        version = self._target_version([(lib["yield_pct"], JAN, 96.0)])
        version.with_user(self.manager).action_approve()
        run = self._run(JAN, version_id=version.id)
        self.assertIn(("cmc.plan.version", version.id), self._usages(run))
        with self.assertRaises(UserError):
            version.with_user(self.manager).action_reset_to_draft()
