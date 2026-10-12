"""RN-003: MCE and cycle time divide by the routed MOs only; lead time keeps all MOs (common.py section 6).

March, press center mixes:
  routed MO   1 P (Cut 30 + Assemble 12, logged 30 + 12, all productive) released Mar 2 08:00 done Mar 2 20:00 (720)
  unrouted MO 4 P (no work order)                                       released Mar 3 08:00 done Mar 4 08:00 (1,440)
  -> MCE 42 / 720 = 5.8333 % (not 42 / 2,160); cycle time 42 / 1 = 42 min/unit (not 42 / 5 = 8.4);
     lead time (720 + 1,440) / 2 / 1,440 = 0.75 days (all MOs).
"""

from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiMrpCase, dt


@tagged("post_install", "-at_install")
class TestKpiMrpRouted(KpiMrpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.routed = cls._done_mo(1.0, dt(3, 2), dt(3, 2, 20), (30.0, 12.0))
        cls.unrouted = cls._done_mo(4.0, dt(3, 3), dt(3, 4), bom=cls.bom_plain)

    def test_routed_preconditions(self):
        # guard: one MO with work orders, one without, both done in the press center
        self.assertTrue(self.routed.workorder_ids)
        self.assertFalse(self.unrouted.workorder_ids)
        self.assertEqual((self.routed | self.unrouted).mapped("cmc_lead_time"), [720.0, 1440.0])

    def test_routed_lead_minutes_feeder(self):
        # RN-003: only the lead time of routed MOs; the full feeder keeps both
        self._refresh()
        self.assertEqual(self._facts("mrp_routed_mo_lead_minutes"), {
            (JAN, self.press): 5760.0, (JAN, self.rework_center): 720.0, (FEB, self.press): 2100.0,
            (MAR, self.press): 720.0,
        })
        self.assertEqual(self._facts("mrp_mo_lead_minutes")[(MAR, self.press)], 2160.0)

    def test_routed_produced_qty_feeder(self):
        # RN-003: produced units of routed MOs only; the full produced feeder keeps both
        self._refresh()
        self.assertEqual(self._facts("mrp_routed_produced_qty"), {
            (JAN, self.press): 20.0, (JAN, self.rework_center): 5.0, (FEB, self.press): 10.0,
            (MAR, self.press): 1.0,
        })
        self.assertEqual(self._facts("mrp_produced_qty")[(MAR, self.press)], 5.0)

    def test_routed_library_kpis(self):
        # RN-003: the two routed denominators ship as measure KPIs of the library
        lib = self._library()
        self.assertRecordValues(lib["mrp_routed_lead_minutes"] | lib["mrp_routed_produced"], [
            {"source_type": "measure", "driver_id": self.env.ref("cmc_kpi_mrp.cmc_measure_routed_mo_lead_minutes").id},
            {"source_type": "measure", "driver_id": self.env.ref("cmc_kpi_mrp.cmc_measure_routed_produced_qty").id},
        ])

    def test_mce_uses_routed_mos_only(self):
        # RN-003: MCE 42 / 720 x 100 (an unrouted MO has no value-added time to compare)
        self._refresh()
        run = self._run(MAR, MAR)
        self.assertAlmostEqual(self._val(run, "mrp_mce", MAR).value, 42.0 / 720.0 * 100.0, places=4)

    def test_cycle_time_uses_routed_mos_only(self):
        # RN-003: cycle 42 min / 1 routed unit (the 4 unrouted units logged no time)
        self._refresh()
        run = self._run(MAR, MAR)
        self.assertAlmostEqual(self._val(run, "mrp_cycle_time", MAR).value, 42.0, places=4)

    def test_lead_time_keeps_all_mos(self):
        # RN-003: the lead-time KPI averages every done MO, routed or not
        self._refresh()
        run = self._run(MAR, MAR)
        self.assertAlmostEqual(self._val(run, "mrp_lead_time", MAR).value, 0.75, places=4)

    def test_fixture_months_unchanged(self):
        # RN-003: every centered MO of the fixture is routed, so Jan / Feb MCE and cycle time do not move
        self._refresh()
        run = self._run()
        self.assertAlmostEqual(self._val(run, "mrp_mce", JAN).value, 15.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_cycle_time", JAN).value, 40.0, places=4)
