"""KPI-01 library: definitions and the five value sources (measure, ratio, variance, formula, engine hook).

Hand numbers (fixture in ``common.py``):
  measure sum: good units Jan 500 + 450 = 950 (a planned fact 99,999 is ignored); avg: cycle days (4 + 6) / 2 = 5.0;
  ratio of two measures: OTD Jan 3 / 4 x 100 = 75.0, Feb 5 / 5 x 100 = 100.0, Mar 0 / 0 (no fact) -> "No data" (DL-010).
  ratio: current_ratio 2026 = (400 + 600) / 500 = 2.0, 2025 = 800 / 200 = 4.0 (financial analysis run).
  variance: rent on "KPI production" Jan (-900) - (-1,000) = +100 -> +10.0 %, Feb -20.0 %; YTD Feb -100 / 2,000 = -5.0 %.
  formula: MCE = processing 2 / (2 + 0.5 + 0.5 + 7) x 100 = 20.0 (Kaplan-Cooper Ch4).
  hook cmc_rate: EUR/h of "KPI assembly" Jan = 45.0 (rate published by a computed closing run).
"""

from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import DEC_2025, FEB, JAN, MAR, KpiCase


@tagged("post_install", "-at_install")
class TestKpiDefinition(KpiCase):
    def test_code_must_be_an_identifier(self):
        # KPI-01: codes are the names of the formulas, so they must be Python identifiers
        with self.assertRaises(ValidationError):
            self._kpi("on-time %", "measure", driver_id=self.good.id)

    def test_code_unique_per_company(self):
        # KPI-01: one code = one KPI in a company library
        self._kpi("dup_code", "measure", driver_id=self.good.id)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._kpi("dup_code", "measure", driver_id=self.good.id)
            self.env.flush_all()

    def test_formula_syntax_checked(self):
        # KPI-01: a broken expression is refused when typed, not at month end
        with self.assertRaises(ValidationError):
            self._kpi("broken", "formula", formula="good_qty / (")

    def test_source_field_required_per_type(self):
        # KPI-01: each source type needs its own source field
        for source_type in ("measure", "ratio", "variance", "formula", "hook"):
            with self.subTest(source_type=source_type), self.assertRaises(ValidationError):
                self._kpi("missing_%s" % source_type, source_type)

    def test_red_tolerance_not_below_amber(self):
        # KPI-01: thresholds are ordered (amber band inside the red one)
        with self.assertRaises(ValidationError):
            self._kpi("bad_bands", "measure", driver_id=self.good.id, amber_tolerance_pct=10.0, red_tolerance_pct=5.0)

    def test_defaults_and_perspectives(self):
        # KPI-01 / KPI-03: higher is better, 0 / 10 % bands; four BSC perspectives
        kpi = self._kpi("defaults", "measure", driver_id=self.good.id)
        self.assertRecordValues(kpi, [{"direction": "higher", "amber_tolerance_pct": 0.0, "red_tolerance_pct": 10.0,
                                       "aggregation": "sum", "scale": 1.0, "company_id": self.company.id}])
        keys = [key for key, _label in self.env["cmc.kpi"]._fields["perspective"].selection]
        self.assertEqual(keys, ["financial", "customer", "internal", "learning"])

    def test_owner_is_the_responsibility_center_owner(self):
        # KPI-03: the KPI owner is the owner of its responsibility center
        kpi = self._kpi("owned", "measure", driver_id=self.good.id, responsibility_id=self.resp_ops.id)
        self.assertEqual(kpi.owner_id, self.owner_ops)

    def test_activity_link_lists_abc_activities(self):
        # KPI-03 (Turney Ch14): a KPI links to the activity it measures when ABC is installed
        models = [model for model, _label in self.env["cmc.kpi"]._fields["activity_ref"]._description_selection(self.env)]
        self.assertEqual("cmc.abc.activity" in models, "cmc.abc.activity" in self.env)


@tagged("post_install", "-at_install")
class TestKpiSources(KpiCase):
    def test_measure_sum_of_actual_facts(self):
        # KPI-02: sum of the month's actual facts; planned facts are never actuals
        kpi = self._kpi("good_qty", "measure", driver_id=self.good.id)
        run = self._run(JAN, MAR)
        self.assertRecordValues(self._val(run, kpi, JAN), [{"value": 950.0, "computable": True}])
        self.assertRecordValues(self._val(run, kpi, FEB), [{"value": 900.0, "computable": True}])

    def test_measure_average(self):
        # KPI-02 / DL-010: cycle time = average of the month's facts; no fact -> "No data" (not computable), not 0
        kpi = self._kpi("cycle_time", "measure", driver_id=self.cycle.id, aggregation="avg", direction="lower")
        run = self._run(JAN, FEB)
        self.assertRecordValues(self._val(run, kpi, JAN), [{"value": 5.0, "computable": True}])
        self.assertRecordValues(self._val(run, kpi, FEB), [{"value": 0.0, "computable": False, "status": "no_data", "score": 0.0}])

    def test_measure_filtered_by_center(self):
        # KPI-01: a measure KPI can be scoped to a center (the facts of other centers are excluded)
        self._fact(self.good, JAN, 70.0, center_id=self.production.id)
        kpi = self._kpi("good_production", "measure", driver_id=self.good.id, center_id=self.production.id)
        self.assertEqual(self._val(self._run(), kpi, JAN).value, 70.0)

    def test_measure_over_denominator_scaled(self):
        # KPI-02 / DL-010: OTD % = on-time shipments / shipments x 100; Mar has no shipment (no fact) -> "No data"
        kpi = self._library()["otd"]
        run = self._run(JAN, MAR)
        self.assertAlmostEqual(self._val(run, kpi, JAN).value, 75.0, places=4)
        self.assertAlmostEqual(self._val(run, kpi, FEB).value, 100.0, places=4)
        self.assertRecordValues(self._val(run, kpi, MAR), [{"computable": False, "status": "no_data", "score": 0.0}])

    def test_ratio_from_computed_fin_analysis(self):
        # KPI-01 / AD-102: the ratio value of the month's year, read from the computed analysis run
        kpi = self._kpi("current_ratio", "ratio", ratio_code="current_ratio")
        run = self._run(DEC_2025, JAN)
        self.assertAlmostEqual(self._val(run, kpi, DEC_2025).value, 4.0, places=6)
        self.assertAlmostEqual(self._val(run, kpi, JAN).value, 2.0, places=6)

    def test_ratio_records_usage_on_the_analysis(self):
        # AD-002: the analysis cannot be reset while a computed KPI run reads it
        self._kpi("current_ratio", "ratio", ratio_code="current_ratio")
        self._run()
        with self.assertRaises(UserError):
            self.fin.action_reset_to_draft()

    def test_ratio_without_analysis_not_computable(self):
        # KPI-01: no computed analysis covering the year -> not computable, never 0
        kpi = self._kpi("current_ratio", "ratio", ratio_code="current_ratio")
        run = self._run(date_from=JAN.replace(year=2027))
        self.assertRecordValues(run.value_ids.filtered(lambda r: r.kpi_id == kpi), [{"computable": False, "status": "none"}])

    def test_variance_month_pct(self):
        # KPI-01: BvA % of center x element; positive = favourable (AD-112)
        kpi = self._library()["rent_bva"]
        run = self._run(JAN, MAR)
        self.assertAlmostEqual(self._val(run, kpi, JAN).value, 10.0, places=4)
        self.assertAlmostEqual(self._val(run, kpi, FEB).value, -20.0, places=4)
        self.assertAlmostEqual(self._val(run, kpi, MAR).value, -5.0, places=4)

    def test_variance_ytd_pct(self):
        # KPI-01: YTD basis Feb = (-2,100 - -2,000) / 2,000 = -5 %
        kpi = self._kpi("rent_ytd", "variance", variance_version_id=self.budget.id, center_id=self.production.id,
                        variance_basis="ytd")
        self.assertAlmostEqual(self._val(self._run(FEB), kpi, FEB).value, -5.0, places=4)

    def test_formula_over_other_kpis(self):
        # KPI-02: MCE = processing / (processing + inspection + move + wait) x 100 = 20 %
        drivers = {}
        for code, qty in (("processing", 2.0), ("inspection", 0.5), ("move_time", 0.5), ("wait", 7.0)):
            drivers[code] = self.env["cmc.cost.driver"].create({"name": code, "code": code.upper(), "measure_kind": "statistical"})
            self._fact(drivers[code], JAN, qty)
            self._kpi(code, "measure", driver_id=drivers[code].id)
        mce = self._kpi("mce", "formula", formula="processing / (processing + inspection + move_time + wait) * 100", unit="percent")
        self.assertAlmostEqual(self._val(self._run(), mce, JAN).value, 20.0, places=6)

    def test_formula_unknown_name_raises_before_writing(self):
        # RN-003 style: a typo is an error naming the KPI and the name, not a silent gap
        self._kpi("good_qty", "measure", driver_id=self.good.id)
        self._kpi("typo", "formula", formula="good_qty / goood_qty")
        run = self._run(compute=False)
        with self.assertRaisesRegex(UserError, "goood_qty"):
            run.action_compute()
        self.assertFalse(run.value_ids)
        self.assertEqual(run.state, "draft")

    def test_formula_cycle_raises(self):
        # KPI-01: a = b + 1, b = a + 1 cannot be evaluated
        self._kpi("kpi_a", "formula", formula="kpi_b + 1")
        self._kpi("kpi_b", "formula", formula="kpi_a + 1")
        with self.assertRaises(UserError):
            self._run()

    def test_formula_on_not_computable_or_zero_division(self):
        # KPI-01: reading a not-computable KPI or dividing by 0 -> not computable (no silent 0)
        self._kpi("cycle_time", "measure", driver_id=self.cycle.id, aggregation="avg")
        self._kpi("zero", "measure", driver_id=self.hours.id)
        on_nc = self._kpi("on_nc", "formula", formula="cycle_time * 2")
        on_zero = self._kpi("on_zero", "formula", formula="1 / zero")
        run = self._run(FEB)
        self.assertRecordValues(self._val(run, on_nc, FEB), [{"computable": False}])
        self.assertRecordValues(self._val(run, on_zero, FEB), [{"computable": False}])

    def test_hook_cmc_rate_center_coefficient(self):
        # KPI-01: EUR/h center coefficient from the rate registry (run-published rate)
        kpi = self._kpi("assembly_rate", "hook", hook_key="cmc_rate", center_id=self.assembly.id,
                        driver_id=self.hours.id, unit="amount", direction="lower", target_value=50.0)
        run = self._run(JAN, FEB)
        self.assertAlmostEqual(self._val(run, kpi, JAN).value, 45.0, places=6)
        self.assertRecordValues(self._val(run, kpi, FEB), [{"computable": False}])
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()

    def test_hook_registered_evaluator_called_once_per_month(self):
        # KPI-01: an engine registers an evaluator; it receives all its KPIs at once (batch)
        Run = self.env["cmc.kpi.run"]
        base = type(Run)._cmc_kpi_evaluators
        calls = []

        def evaluator(run, kpis, month):
            calls.append((month, kpis))
            return {kpi.id: 7.0 * len(kpis) for kpi in kpis}

        k1 = self._kpi("ext_one", "hook", hook_key="wp14_engine")
        k2 = self._kpi("ext_two", "hook", hook_key="wp14_engine")
        with patch.object(type(Run), "_cmc_kpi_evaluators", lambda self: {**base(self), "wp14_engine": evaluator}):
            run = self._run(JAN, FEB)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], k1 | k2)
        self.assertEqual(self._val(run, k1, JAN).value, 14.0)

    def test_hook_unknown_key_raises(self):
        # KPI-01: a KPI pointing at an engine that is not installed is an error naming the key
        self._kpi("ghost", "hook", hook_key="not_installed_engine")
        with self.assertRaisesRegex(UserError, "not_installed_engine"):
            self._run()
