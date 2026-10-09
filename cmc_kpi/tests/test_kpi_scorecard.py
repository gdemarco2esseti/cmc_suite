"""KPI-03 balanced scorecard: perspectives, weights = 100 per level, weighted scores, cascade.

Scorecard "KPI company": financial 40 (current_ratio 50, rent_bva 50), customer 30 (otd 100), internal 30
(yield_pct 60, cycle_time 40); yield targets 96 from the version. KPI scores Jan:
  current_ratio 2.0 vs 1.5 -> +33.3333 % -> 133.3333; rent_bva +10 (target 0) -> 110; otd 78.9474;
  yield 98.9583; cycle_time 116.6667.
  financial = (133.3333 x 50 + 110 x 50) / 100 = 121.6667; customer = 78.9474;
  internal = (98.9583 x 60 + 116.6667 x 40) / 100 = 106.0417;
  overall = 0.4 x 121.6667 + 0.3 x 78.9474 + 0.3 x 106.0417 = 104.1634.
Mar: otd and cycle_time not computable -> customer not computable, internal = yield 102.0833;
  financial = (133.3333 + 95) / 2 = 114.1667 (rent -5 %); overall renormalised on 70 = 108.9881.
Cascade "ops_excellence" = yield 60 + otd 40: Jan 0.6 x 98.9583 + 0.4 x 78.9474 = 90.9539 (target 100 ->
  gap -9.0461 % -> amber); Mar otd not computable -> yield score alone 102.0833.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiCase


@tagged("post_install", "-at_install")
class TestKpiScorecard(KpiCase):
    def _scorecard(self, lib, perspectives=None, lines=None, **vals):
        perspectives = perspectives or {"financial": 40.0, "customer": 30.0, "internal": 30.0}
        lines = lines or [("current_ratio", 50.0), ("rent_bva", 50.0), ("otd", 100.0), ("yield_pct", 60.0), ("cycle_time", 40.0)]
        return self.env["cmc.kpi.scorecard"].create({
            "name": "KPI company", **vals,
            "perspective_ids": [(0, 0, {"perspective": key, "weight": weight}) for key, weight in perspectives.items()],
            "line_ids": [(0, 0, {"kpi_id": lib[code].id, "weight": weight}) for code, weight in lines],
        })

    def _scored_run(self, date_from=JAN, date_to=None):
        lib = self._library()
        scorecard = self._scorecard(lib)
        version = self._target_version([(lib["yield_pct"], month, 96.0) for month in (JAN, FEB, MAR)])
        run = self._run(date_from, date_to, version_id=version.id)
        return lib, scorecard, run

    def _result(self, run, scorecard, month, perspective=False):
        rows = run.scorecard_result_ids.filtered(
            lambda r: r.scorecard_id == scorecard and r.date == month and r.perspective == perspective
        )
        self.assertEqual(len(rows), 1, "expected one result for %s %s" % (month, perspective))
        return rows

    def test_line_perspective_follows_the_kpi(self):
        # KPI-03: a KPI sits in the perspective of its definition
        lib = self._library()
        scorecard = self._scorecard(lib)
        line = scorecard.line_ids.filtered(lambda line: line.kpi_id == lib["otd"])
        self.assertEqual(line.perspective, "customer")

    def test_perspective_scores(self):
        # KPI-03: weighted score per perspective
        _lib, scorecard, run = self._scored_run()
        for perspective, weight, score in (("financial", 40.0, 121.666667), ("customer", 30.0, 78.947368), ("internal", 30.0, 106.041667)):
            row = self._result(run, scorecard, JAN, perspective)
            self.assertEqual(row.weight, weight)
            self.assertAlmostEqual(row.score, score, places=3)

    def test_overall_score(self):
        # KPI-03: overall = perspectives weighted by their weights
        _lib, scorecard, run = self._scored_run()
        self.assertAlmostEqual(self._result(run, scorecard, JAN).score, 104.163377, places=3)

    def test_not_computable_kpis_renormalise(self):
        # KPI-03: a KPI without data is left out (weights renormalised), never scored 0
        _lib, scorecard, run = self._scored_run(MAR)
        self.assertRecordValues(self._result(run, scorecard, MAR, "customer"), [{"computable": False}])
        self.assertAlmostEqual(self._result(run, scorecard, MAR, "internal").score, 102.083333, places=3)
        self.assertAlmostEqual(self._result(run, scorecard, MAR, "financial").score, 114.166667, places=3)
        self.assertAlmostEqual(self._result(run, scorecard, MAR).score, 108.988095, places=3)

    def test_perspective_weights_must_total_100(self):
        # KPI-03: weights per level = 100 %
        lib = self._library()
        self._scorecard(lib, perspectives={"financial": 40.0, "customer": 30.0, "internal": 20.0})
        with self.assertRaisesRegex(UserError, "KPI company"):
            self._run()

    def test_kpi_weights_must_total_100_per_perspective(self):
        # KPI-03: KPI weights inside a perspective = 100 %
        lib = self._library()
        lines = [("current_ratio", 50.0), ("rent_bva", 40.0), ("otd", 100.0), ("yield_pct", 60.0), ("cycle_time", 40.0)]
        self._scorecard(lib, lines=lines)
        with self.assertRaises(UserError):
            self._run()

    def test_kpi_in_unweighted_perspective_refused(self):
        # KPI-03: a KPI of a perspective with no weight would never count
        lib = self._library()
        lib["good_qty"].perspective = "learning"
        lines = [("current_ratio", 50.0), ("rent_bva", 50.0), ("otd", 100.0), ("yield_pct", 60.0), ("cycle_time", 40.0), ("good_qty", 100.0)]
        self._scorecard(lib, lines=lines)
        with self.assertRaises(UserError):
            self._run()

    def test_archived_scorecard_not_computed(self):
        # KPI-03: only active scorecards of the company are scored by default
        lib = self._library()
        self._scorecard(lib, active=False)
        self.assertFalse(self._run().scorecard_result_ids)

    def test_cascade_parent_from_children(self):
        # KPI-03: the parent KPI scores the weighted children (60 / 40)
        lib = self._library()
        parent = self._kpi("ops_excellence", "cascade", perspective="internal", unit="percent")
        lib["yield_pct"].write({"parent_id": parent.id, "cascade_weight": 60.0})
        lib["otd"].write({"parent_id": parent.id, "cascade_weight": 40.0})
        version = self._target_version([(lib["yield_pct"], month, 96.0) for month in (JAN, FEB, MAR)])
        run = self._run(JAN, MAR, version_id=version.id)
        jan = self._val(run, parent, JAN)
        self.assertAlmostEqual(jan.value, 90.953947, places=3)
        self.assertRecordValues(jan, [{"target": 100.0, "status": "amber"}])
        self.assertAlmostEqual(self._val(run, parent, MAR).value, 102.083333, places=3)
        self.assertEqual(parent.child_ids, lib["yield_pct"] | lib["otd"])

    def test_cascade_weights_must_total_100(self):
        # KPI-03: children weights of a parent = 100 %
        lib = self._library()
        parent = self._kpi("ops_excellence", "cascade")
        lib["yield_pct"].write({"parent_id": parent.id, "cascade_weight": 60.0})
        lib["otd"].write({"parent_id": parent.id, "cascade_weight": 30.0})
        with self.assertRaisesRegex(UserError, "ops_excellence|KPI ops_excellence"):
            self._run()
