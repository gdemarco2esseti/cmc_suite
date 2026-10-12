"""RN-002: two operators timing the same work order at once add value once (common.py section 6).

March: a 1-unit MO on a one-operation routing (Cut 30 min/unit) released Mar 2 08:00; operators A and B both
start their timer at 08:00 and stop it at 08:08 (two productive entries of 8 min, overlapping); MO closed 08:10 (lead 10).
  core work order duration (merged) 8; value added must be 8 (not 16) -> MCE 8 / 10 = 80 % (not 160 %).
"""

from contextlib import contextmanager
from unittest.mock import patch

from freezegun import freeze_time

from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import MAR, KpiMrpCase, dt


@contextmanager
def shop_clock(cr, at):
    """Frozen clock for Python AND the cursor: core ``get_duration`` reads the running timers at ``cr.now()`` (the
    transaction timestamp, not frozen by freezegun); unpatched, the second operator's start sees the first timer as
    months long and books it as a performance loss instead of productive time."""
    with freeze_time(at), patch.object(cr, "now", return_value=at):
        yield


@tagged("post_install", "-at_install")
class TestKpiMrpOverlap(KpiMrpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator_b = new_test_user(cls.env, login="kpimrp_mrp_b", groups="base.group_user,mrp.group_mrp_user")
        cls.bom_cut = cls._bom(cls.gear, cls.blank, [(cls.wc_cut, "Cut", 30.0)])
        cls.mo = cls.env["mrp.production"].create({
            "product_id": cls.gear.id, "product_qty": 1.0, "bom_id": cls.bom_cut.id,
            "cmc_center_id": cls.press.id, "date_start": dt(3, 2),
        })
        # the real shop-floor path: each operator starts and pauses his own timer - per employee with the
        # enterprise shop floor (mrp_workorder: timers keyed by employee), per user with community mrp
        by_employee = hasattr(cls.env["mrp.workorder"], "start_employee")
        if by_employee:
            cls.operators = cls.env["hr.employee"].create([{"name": "KPIMRP operator A"}, {"name": "KPIMRP operator B"}])
        with shop_clock(cls.env.cr, dt(3, 2)):
            cls.mo.action_confirm()
            cls.workorder = cls.mo.workorder_ids
            if by_employee:
                for employee in cls.operators:
                    cls.workorder.start_employee(employee.id)
            else:
                cls.workorder.with_user(cls.mrp_user).button_start()
                cls.workorder.with_user(cls.operator_b).button_start()
        with shop_clock(cls.env.cr, dt(3, 2, 8, 8)):
            if by_employee:
                cls.workorder.stop_employee(cls.operators.ids)
            else:
                cls.workorder.with_user(cls.mrp_user).button_pending()
                cls.workorder.with_user(cls.operator_b).button_pending()
        with freeze_time(dt(3, 2, 8, 10)):
            cls._close(cls.mo, 1.0)

    def test_overlap_preconditions(self):
        # guard: two productive entries of 8 min by two operators; core merges them into 8 min of duration
        times = self.workorder.time_ids
        self.assertEqual(len(times), 2)
        if hasattr(self, "operators"):
            self.assertEqual(times.employee_id, self.operators)
        else:
            self.assertEqual(times.user_id, self.mrp_user | self.operator_b)
        self.assertEqual(times.mapped("loss_type"), ["productive", "productive"])
        self.assertEqual(sum(times.mapped("duration")), 16.0)
        self.assertEqual(self.workorder.duration, 8.0)
        self.assertEqual(self.mo.cmc_lead_time, 10.0)

    def test_productive_minutes_field_merges_overlaps(self):
        # RN-002: stored merged productive time on the work order
        field = self.env["mrp.workorder"]._fields.get("cmc_productive_minutes")
        self.assertTrue(field and field.store, "mrp.workorder.cmc_productive_minutes must be a stored field")
        self.assertEqual(self.workorder.cmc_productive_minutes, 8.0)

    def test_productive_minutes_exclude_losses(self):
        # RN-002: MO1 Assemble of the fixture: 144 productive + 28 performance -> 144 value-added minutes
        assemble = self.mo1.workorder_ids.sorted(lambda wo: wo.operation_id.id)[1]
        self.assertEqual(assemble.cmc_productive_minutes, 144.0)

    def test_value_added_counts_overlap_once(self):
        # RN-002: value-added fact = 8, never 16 (the sum of the concurrent timers)
        self._refresh()
        self.assertEqual(self._facts("mrp_value_added_minutes")[(MAR, self.press)], 8.0)

    def test_mce_never_above_hundred_with_concurrent_operators(self):
        # RN-002: MCE = 8 / 10 = 80 %, a ratio over the throughput time cannot exceed 100 %
        self._refresh()
        run = self._run(MAR, MAR)
        mce = self._val(run, "mrp_mce", MAR).value
        self.assertLessEqual(mce, 100.0)
        self.assertAlmostEqual(mce, 80.0, places=4)
