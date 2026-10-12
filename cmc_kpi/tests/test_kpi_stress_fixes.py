"""Stress test 2026-10 (corsia B) - DL-010 (KPI without data), FX-018 (ROS unit), FX-017 (it_IT statusbar) on cmc_kpi.

CONTRACT (new or changed, binding for the developer)
====================================================
DL-010: ``cmc.kpi.value.status`` gains ``no_data`` ("No data"). A computable row whose value is 0 with a 0 target,
  or a ``measure`` KPI with NO fact at all in the month (no source), gets ``status = "no_data"``, ``score`` 0,
  never "On target"; a real value against a 0 target (variance KPIs) keeps the normal traffic light.
FX-018: a ``ratio`` KPI takes the unit of its ``cmc.fin.ratio`` (``unit`` computed from ``ratio_code``, stored,
  readonly=False): ROS (``cmc_fin_analysis.ratio_ros``, percent) -> ``percent``; the KPI value is the same fraction
  as the ratio value (one convention for ROS in KPI and Indici).
FX-017: ``cmc.kpi.run.state`` reads "Bozza / Calcolato" in it_IT.
"""

from odoo.tests import tagged

from .common import JAN, KpiCase


@tagged("post_install", "-at_install")
class TestKpiStressFixes(KpiCase):
    def _empty_driver(self):
        return self.env["cmc.cost.driver"].create(
            {"name": "KPI dl010 no production", "code": "KPIDL010", "company_id": False, "measure_kind": "statistical"}
        )

    def _single(self, kpi, user=None):
        run = self._run(JAN, kpi_ids=[(6, 0, kpi.ids)], user=user)
        return self._val(run, kpi, JAN)

    def test_dl010_status_has_no_data_option(self):
        # DL-010: "No data" is a state of its own, distinct from "On target" and "Not computable"
        selection = dict(self.env["cmc.kpi.value"].fields_get(["status"])["status"]["selection"])
        self.assertIn("no_data", selection)

    def test_dl010_zero_value_zero_target_is_no_data(self):
        # DL-010 / SC-048: a production KPI without production (0 vs 0) is not "On target"
        kpi = self._kpi("dl010_zero", "measure", driver_id=self._empty_driver().id, target_value=0.0)
        row = self._single(kpi, user=self.user)
        self.assertRecordValues(row, [{"status": "no_data", "score": 0.0}])

    def test_dl010_no_source_is_no_data(self):
        # DL-010: no fact at all in the month = no source: not a red light either
        kpi = self._kpi("dl010_nosrc", "measure", driver_id=self._empty_driver().id, target_value=10.0)
        row = self._single(kpi, user=self.user)
        self.assertEqual(row.status, "no_data")

    def test_dl010_real_value_against_zero_target_keeps_light(self):
        # KPI-01 regression: a real value against a 0 target still gets the traffic light (variance KPIs)
        kpi = self._kpi("dl010_real", "measure", driver_id=self.good.id, target_value=0.0)
        row = self._single(kpi)
        self.assertEqual(row.status, "green")

    def test_fx018_ratio_kpi_takes_ratio_unit(self):
        # FX-018: ROS in KPI and in Indici has the same unit (percent of the same fraction)
        ros = self.env.ref("cmc_fin_analysis.ratio_ros")
        kpi = self._kpi("ros", "ratio", ratio_code="ros")
        self.assertEqual(kpi.unit, ros.unit)
        self.assertEqual(kpi.unit, "percent")

    def test_fx018_ratio_kpi_value_is_the_ratio_fraction(self):
        # FX-018: the KPI does not rescale the ratio: same fraction as the financial analysis value
        kpi = self._kpi("ros", "ratio", ratio_code="ros")
        ratio = self.env["cmc.fin.ratio.value"].search([("run_id", "=", self.fin.id), ("code", "=", "ros"), ("year", "=", JAN.year)])
        self.assertEqual(len(ratio), 1)
        row = self._single(kpi)
        self.assertAlmostEqual(row.value, ratio.value, places=4)

    def test_fx017_kpi_run_statusbar_italian(self):
        # FX-017: "Bozza / Calcolato" on the KPI run as on ABC and closing
        selection = dict(self.env["cmc.kpi.run"].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
        self.assertEqual(selection, {"draft": "Bozza", "done": "Calcolato"})
