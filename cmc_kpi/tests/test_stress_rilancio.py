"""Stress test 2026-10 (rilancio) - regression of the fixes on cmc_kpi.

- DL-010 bis: a ``formula`` KPI whose input KPIs ALL have no data in the month (e.g. produced - reworked, both
  measures without driver facts) is ``no_data`` too, never "On target"; a formula over inputs with data that returns
  a real 0 against a 0 target stays on target (0 ppm is a result).
- FX-018 bis: ``cmc.kpi.value.value_display`` / ``target_display`` (non stored) show a percent ``ratio`` KPI as a
  percentage of its fraction (0.9076 -> "90.76 %"), any other KPI with 4 digits and no suffix.
"""

from odoo.tests import tagged
from odoo.tools import formatLang

from .common import JAN, KpiCase


@tagged("post_install", "-at_install")
class TestKpiStressRilancio(KpiCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # why: the displays are asserted in en_US figures; the database may only have it_IT active
        cls.env["res.lang"]._activate_lang("en_US")
        Driver = cls.env["cmc.cost.driver"]
        # drivers without any fact: the month has no production at all
        cls.empty_produced = Driver.create({"name": "KPI rl produced", "code": "KPIRLPRD", "company_id": False, "measure_kind": "statistical"})
        cls.empty_reworked = Driver.create({"name": "KPI rl reworked", "code": "KPIRLRWK", "company_id": False, "measure_kind": "statistical"})

    def _formula_row(self, formula, inputs, target=0.0):
        kpi = self._kpi("rl_formula", "formula", formula=formula, target_value=target)
        run = self._run(JAN, kpi_ids=[(6, 0, kpi.ids)], user=self.user)
        return self._val(run, kpi, JAN), {code: self._val(run, inp, JAN) for code, inp in inputs.items()}

    def test_formula_over_inputs_without_data_is_no_data(self):
        # DL-010 bis: produced - reworked with no fact for either input is "No data", not "On target" (0 vs 0)
        produced = self._kpi("rl_produced", "measure", driver_id=self.empty_produced.id, aggregation="sum")
        reworked = self._kpi("rl_reworked", "measure", driver_id=self.empty_reworked.id, aggregation="sum")
        row, inputs = self._formula_row("rl_produced - rl_reworked", {"produced": produced, "reworked": reworked})
        self.assertRecordValues(inputs["produced"] | inputs["reworked"], [{"status": "no_data"}, {"status": "no_data"}])
        self.assertRecordValues(row, [{"status": "no_data", "computable": False, "score": 0.0}])
        self.assertNotEqual(row.status, "green")

    def test_formula_zero_with_data_against_zero_target_stays_on_target(self):
        # DL-010 bis regression: inputs with data returning a real 0 against a 0 target keep the traffic light
        good = self._kpi("rl_good", "measure", driver_id=self.good.id, aggregation="sum")
        row, inputs = self._formula_row("rl_good - rl_good", {"good": good})
        self.assertNotEqual(inputs["good"].status, "no_data")
        self.assertRecordValues(row, [{"status": "green", "computable": True, "value": 0.0}])

    def test_formula_with_one_input_with_data_is_not_no_data(self):
        # DL-010 bis precision: only ALL inputs empty make the formula empty; one input with data still measures
        good = self._kpi("rl_good", "measure", driver_id=self.good.id, aggregation="sum")
        empty = self._kpi("rl_empty", "measure", driver_id=self.empty_produced.id, aggregation="sum")
        row, _inputs = self._formula_row("rl_good + rl_empty", {"good": good, "empty": empty})
        self.assertNotEqual(row.status, "no_data")
        self.assertTrue(row.computable)
        self.assertAlmostEqual(row.value, 950.0)

    def test_percent_ratio_display_is_percentage_of_fraction(self):
        # FX-018 bis: a percent ratio KPI shows 0.9076 as "90.76 %" and the target 0.6 as "60.00 %"
        kpi = self._kpi("rl_ros", "ratio", ratio_code="ros")
        self.assertEqual(kpi.unit, "percent")
        value = self.env["cmc.kpi.value"].with_context(lang="en_US").new({"kpi_id": kpi.id, "value": 0.9076, "target": 0.6})
        self.assertEqual(value.value_display, "90.76 %")
        self.assertEqual(value.target_display, "60.00 %")

    def test_number_display_has_four_digits_no_suffix(self):
        # FX-018 bis: a plain number keeps 4 digits and no unit suffix
        kpi = self._kpi("rl_num", "measure", driver_id=self.good.id, unit="number")
        value = self.env["cmc.kpi.value"].with_context(lang="en_US").new({"kpi_id": kpi.id, "value": 1.5, "target": 2.0})
        self.assertEqual(value.value_display, "1.5000")
        self.assertEqual(value.target_display, "2.0000")

    def test_computed_ros_row_display_matches_ratio_percentage(self):
        # FX-018 bis end-to-end: the row produced by the run shows the ROS fraction as a percentage
        kpi = self._kpi("rl_ros", "ratio", ratio_code="ros", target_value=0.6)
        run = self._run(JAN, kpi_ids=[(6, 0, kpi.ids)], user=self.user)
        row = self._val(run, kpi, JAN).with_context(lang="en_US")
        self.assertEqual(row.value_display, formatLang(row.env, row.value * 100.0, digits=2) + " %")
        self.assertEqual(row.target_display, "60.00 %")

    # ---- RN-007: percent targets typed as read ----------------------------------------------------------

    def test_rn007_manager_types_percent_ratio_target_as_percentage(self):
        # RN-007: the manager types 15 on a percent ratio KPI and gets 15 %, stored as the fraction 0.15
        kpi = self._kpi("rl_ros", "ratio", ratio_code="ros")
        form_kpi = kpi.with_user(self.manager)
        form_kpi.target_input = 15.0
        self.assertAlmostEqual(kpi.target_value, 0.15)
        self.assertAlmostEqual(kpi.target_input, 15.0)

    def test_rn007_non_fraction_targets_are_unchanged(self):
        # RN-007: a number KPI (and a 0..100 percent formula) keeps the typed figure as is
        number = self._kpi("rl_num", "measure", driver_id=self.good.id, unit="number")
        percent_formula = self._kpi("rl_pct", "formula", formula="100", unit="percent")
        (number | percent_formula).with_user(self.manager).write({"target_input": 12.5})
        self.assertRecordValues(number | percent_formula, [{"target_value": 12.5}, {"target_value": 12.5}])

    def test_rn007_version_target_typed_as_percentage(self):
        # RN-007: the same rule on the plan-version targets
        kpi = self._kpi("rl_ros", "ratio", ratio_code="ros")
        version = self._target_version([(kpi, JAN, 0.0)])
        target = version.kpi_target_ids.with_user(self.manager)
        target.target_input = 15.0
        self.assertAlmostEqual(target.target_value, 0.15)

    # ---- RN-008: the pack prints the figures as the screens --------------------------------------------

    def test_rn008_pack_prints_percent_ratio_kpi_as_percentage(self):
        # RN-008: the management pack carries the display figures; the PDF prints them, never the raw fraction
        kpi = self._kpi("rl_ros", "ratio", ratio_code="ros", target_value=0.6)
        run = self._run(JAN, kpi_ids=[(6, 0, kpi.ids)], user=self.user)
        row = next(row for row in run.with_context(lang="en_US")._cmc_pack_data(JAN)["kpis"] if row["kpi"] == kpi)
        self.assertEqual(row["target_display"], "60.00 %")
        arch = self.env.ref("cmc_kpi.report_cmc_kpi_pack_document").arch_db
        for key in ("value_display", "target_display"):
            self.assertIn(f"row['{key}']", arch)
        self.assertNotIn("t-out=\"row['target']\"", arch)
        self.assertNotIn("t-out=\"row['value']\"", arch)
