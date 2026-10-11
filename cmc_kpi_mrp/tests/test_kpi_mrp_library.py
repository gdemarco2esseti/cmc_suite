"""KPI-02 residual: the shipped MRP KPI library computed by a cmc_kpi run over the real feeders (common.py numbers).

  Jan: cycle 1,000 / 25 = 40.0 min/unit; lead 6,480 / 3 / 1,440 = 1.5 days; PPM 1 / 25 x 1e6 = 40,000;
       first pass 25 - 5 = 20, FPY (20 - 5 - 1) / 20 x 100 = 70.0; MCE 972 / 6,480 x 100 = 15.0.
  Feb: cycle 420 / 10 = 42.0; lead 2,100 / 1 / 1,440 days; PPM 0.0; FPY 100.0; MCE 420 / 2,100 x 100 = 20.0.
  Mar: no MO -> every ratio not computable (never a silent 0).
"""

from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiMrpCase

LIBRARY = {
    # code: (source_type, unit, direction, perspective)
    "mrp_produced": ("measure", "number", "higher", "internal"),
    "mrp_scrapped": ("measure", "number", "lower", "internal"),
    "mrp_reworked": ("measure", "number", "lower", "internal"),
    "mrp_wo_minutes": ("measure", "number", "lower", "internal"),
    "mrp_value_added": ("measure", "number", "higher", "internal"),
    "mrp_lead_minutes": ("measure", "number", "lower", "internal"),
    "mrp_mo_done": ("measure", "number", "higher", "internal"),
    "mrp_routed_lead_minutes": ("measure", "number", "lower", "internal"),
    "mrp_routed_produced": ("measure", "number", "higher", "internal"),
    "mrp_first_pass": ("formula", "number", "higher", "internal"),
    "mrp_cycle_time": ("formula", "number", "lower", "internal"),
    "mrp_lead_time": ("formula", "days", "lower", "internal"),
    "mrp_ppm": ("formula", "number", "lower", "internal"),
    "mrp_fpy": ("formula", "percent", "higher", "internal"),
    "mrp_mce": ("formula", "percent", "higher", "internal"),
    "delivery_shipments": ("measure", "number", "higher", "customer"),
    "delivery_lead_minutes": ("measure", "number", "lower", "customer"),
    "delivery_lead_time": ("formula", "days", "lower", "customer"),
}
MEASURE_DRIVERS = {
    "mrp_produced": "cmc_mrp.cmc_measure_produced_qty",
    "mrp_scrapped": "cmc_mrp.cmc_measure_scrap_qty",
    "mrp_reworked": "cmc_kpi_mrp.cmc_measure_rework_qty",
    "mrp_wo_minutes": "cmc_kpi_mrp.cmc_measure_wo_minutes",
    "mrp_value_added": "cmc_kpi_mrp.cmc_measure_value_added_minutes",
    "mrp_lead_minutes": "cmc_kpi_mrp.cmc_measure_mo_lead_minutes",
    "mrp_mo_done": "cmc_kpi_mrp.cmc_measure_mo_done",
    "mrp_routed_lead_minutes": "cmc_kpi_mrp.cmc_measure_routed_mo_lead_minutes",
    "mrp_routed_produced": "cmc_kpi_mrp.cmc_measure_routed_produced_qty",
    "delivery_shipments": "cmc_kpi.cmc_measure_shipments",
    "delivery_lead_minutes": "cmc_kpi_mrp.cmc_measure_delivery_lead_minutes",
}


@tagged("post_install", "-at_install")
class TestKpiMrpLibrary(KpiMrpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Fact._cmc_refresh_feeders(JAN, MAR.replace(day=31), company=cls.company)

    def test_library_codes_and_attributes(self):
        # KPI-02: the shipped library, one KPI per code, in the company, with its unit / direction / perspective
        lib = self._library()
        self.assertEqual(set(lib), set(LIBRARY))
        kpis = self.env["cmc.kpi"].concat(*[lib[code] for code in LIBRARY])
        self.assertRecordValues(kpis, [
            {"code": code, "source_type": src, "unit": unit, "direction": direction, "perspective": persp,
             "company_id": self.company.id, "active": True}
            for code, (src, unit, direction, persp) in LIBRARY.items()
        ])

    def test_library_measures_read_the_feeder_drivers(self):
        # KPI-02 / AD-102: the measure KPIs read the catalogue measures, summed
        lib = self._library()
        kpis = self.env["cmc.kpi"].concat(*[lib[code] for code in MEASURE_DRIVERS])
        self.assertRecordValues(kpis, [
            {"driver_id": self.env.ref(xmlid).id, "aggregation": "sum", "denominator_driver_id": False}
            for xmlid in MEASURE_DRIVERS.values()
        ])

    def test_loader_is_idempotent(self):
        # contract: loading twice returns the same KPIs, never duplicates
        first = self._library()
        second = self._library()
        self.assertEqual({c: k.id for c, k in first.items()}, {c: k.id for c, k in second.items()})
        self.assertEqual(self.env["cmc.kpi"].search_count([("code", "=", "mrp_fpy"), ("company_id", "=", self.company.id)]), 1)

    def test_not_loaded_at_install(self):
        # contract: no KPI is created at install, so the runs with an empty selection of existing suites do not change
        self.assertFalse(self.env["cmc.kpi"].with_context(active_test=False).search(
            [("code", "in", list(LIBRARY)), ("company_id", "=", self.company.id)]))

    def test_cycle_time_per_unit(self):
        # KPI-02 cycle time = work-order minutes / produced units
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_cycle_time", JAN).value, 40.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_cycle_time", FEB).value, 42.0, places=4)

    def test_manufacturing_lead_time_days(self):
        # KPI-02 lead time = average (date_finished - date_start) of the done MOs, in days
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_lead_time", JAN).value, 1.5, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_lead_time", FEB).value, 2100.0 / 1440.0, places=4)

    def test_ppm(self):
        # KPI-02 PPM = scrapped / produced x 1,000,000; a month without scrap is 0 ppm (computable)
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_ppm", JAN).value, 40000.0, places=2)
        feb = self._val(run, "mrp_ppm", FEB)
        self.assertEqual((feb.computable, feb.value), (True, 0.0))

    def test_first_pass_yield(self):
        # KPI-02 FPY = (first pass - reworked - scrapped) / first pass, first pass = produced - rework output
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_first_pass", JAN).value, 20.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_fpy", JAN).value, 70.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_fpy", FEB).value, 100.0, places=4)

    def test_manufacturing_cycle_efficiency(self):
        # KPI-02 MCE (Kaplan-Cooper Ch4) = value-added (productive) minutes / MO lead minutes x 100
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_mce", JAN).value, 15.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_mce", FEB).value, 20.0, places=4)

    def test_month_without_production_not_computable(self):
        # cmc_kpi rule: a ratio over nothing is not computable, never a silent 0; DL-010: its status is "No data"
        run = self._run(JAN, MAR)
        for code in ("mrp_cycle_time", "mrp_lead_time", "mrp_ppm", "mrp_fpy", "mrp_mce"):
            row = self._val(run, code, MAR)
            self.assertFalse(row.computable, code)
            self.assertEqual(row.status, "no_data", code)

    def test_center_filtered_cycle_time(self):
        # KPI-02 per center: the same measure filtered on the rework center = 192 min / 5 units = 38.4
        lib = self._library()
        rework_minutes = self.env["cmc.kpi"].create({
            "name": "Rework minutes per unit", "code": "rework_cycle", "source_type": "measure",
            "driver_id": self.env.ref("cmc_kpi_mrp.cmc_measure_wo_minutes").id, "center_id": self.rework_center.id,
            "denominator_driver_id": self.env.ref("cmc_mrp.cmc_measure_produced_qty").id,
        })
        run = self._run(JAN, JAN, kpis=lib["mrp_cycle_time"] | rework_minutes)
        self.assertAlmostEqual(self._val(run, "rework_cycle", JAN).value, 38.4, places=4)
