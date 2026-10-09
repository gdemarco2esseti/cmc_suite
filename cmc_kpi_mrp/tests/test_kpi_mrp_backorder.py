"""RN-001: a partial close with backorder keeps the rework marker and the original release (common.py section 6).

March scenario on top of the fixture (press = routed, rework = no routing, it does not matter for FPY / lead):
  press MO 20 P released Mar 2 08:00, done Mar 3 08:00; scrap 1 P of it on Mar 4
  rework MO 5 P released Mar 9 08:00: partial close of 3 on Mar 10 08:00 (1,440), backorder of 2 closed
    Mar 12 20:00 (lead from the ORIGINAL release: 3.5 days = 5,040)
  -> produced 25, reworked 3 + 2 = 5, first pass 20, FPY (20 - 5 - 1) / 20 = 70.0 % (as unsplit);
     rework MOs done 2 (backorder counted as a separate MO, pinned), rework lead 1,440 + 5,040 = 6,480.
"""

from freezegun import freeze_time

from odoo.tests import tagged

from .common import JAN, MAR, KpiMrpCase, dt


@tagged("post_install", "-at_install")
class TestKpiMrpBackorder(KpiMrpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.press_mar = cls._done_mo(20.0, dt(3, 2), dt(3, 3), (600.0, 240.0))
        cls._scrap(cls.press_mar, 1.0, dt(3, 4))
        cls.rework_mo = cls.env["mrp.production"].create({
            "product_id": cls.gear.id, "product_qty": 5.0, "bom_id": cls.bom_plain.id,
            "cmc_center_id": cls.rework_center.id, "cmc_is_rework": True, "date_start": dt(3, 9),
        })
        with freeze_time(dt(3, 9)):
            cls.rework_mo.action_confirm()
        with freeze_time(dt(3, 10)):
            cls.backorder = cls._close_partial(cls.rework_mo, 3.0)
        with freeze_time(dt(3, 12, 20)):
            cls._close(cls.backorder, 2.0)

    def test_split_preconditions(self):
        # guard: the core split produced one done original of 3 and one done backorder of 2
        self.assertEqual(len(self.backorder), 1)
        self.assertRecordValues(self.rework_mo | self.backorder, [
            {"state": "done", "qty_produced": 3.0, "date_finished": dt(3, 10)},
            {"state": "done", "qty_produced": 2.0, "date_finished": dt(3, 12, 20)},
        ])

    def test_backorder_inherits_rework_flag_and_release(self):
        # RN-001: the backorder re-processes the same failed units, released with the original order
        self.assertRecordValues(self.backorder, [{"cmc_is_rework": True, "cmc_date_released": dt(3, 9),
                                                  "cmc_center_id": self.rework_center.id}])

    def test_backorder_lead_time_from_original_release(self):
        # RN-001: the units of the backorder waited since the original release (Mar 9 08:00 -> Mar 12 20:00)
        self.assertEqual((self.rework_mo | self.backorder).mapped("cmc_lead_time"), [1440.0, 5040.0])
        self._refresh()
        self.assertEqual(self._facts("mrp_mo_lead_minutes")[(MAR, self.rework_center)], 6480.0)

    def test_backorder_counted_as_separate_done_mo(self):
        # RN-001 (pinned): mrp_mo_done counts the backorder as its own done MO
        self._refresh()
        self.assertEqual(self._facts("mrp_mo_done")[(MAR, self.rework_center)], 2.0)

    def test_split_rework_feeds_whole_rework_quantity(self):
        # RN-001: rework 3 + 2 = 5, never 3 (the backorder output is rework, not first pass)
        self._refresh()
        self.assertEqual(self._facts("mrp_rework_qty"), {(JAN, self.rework_center): 5.0,
                                                         (MAR, self.rework_center): 5.0})

    def test_fpy_unchanged_by_split(self):
        # RN-001: FPY of the March scenario = (20 - 5 - 1) / 20 = 70 %, as the unsplit January fixture
        self._refresh()
        run = self._run(MAR, MAR)
        self.assertAlmostEqual(self._val(run, "mrp_first_pass", MAR).value, 20.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_fpy", MAR).value, 70.0, places=4)
