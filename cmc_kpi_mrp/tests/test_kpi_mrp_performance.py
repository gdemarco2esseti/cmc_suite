"""AD-114 cost of access: the MRP feeders group in SQL, whatever the number of MOs / work orders / time entries.

Small = the fixture (4 done MOs in the CMC centers); big = the same + 15 done MOs with 2 work orders each
(30 work orders, 30+ time entries). A per-MO / per-work-order read (N+1, or a non-stored lead time computed in
Python) makes the big refresh cost more than the small one + TOLERANCE.
"""

from odoo.tests import tagged

from .common import JAN, MAR_END, MRP_KEYS, KpiMrpCase, dt

TOLERANCE = 3


@tagged("post_install", "-at_install")
class TestKpiMrpPerformance(KpiMrpCase):
    def _cost(self):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        self.Fact._cmc_refresh_feeders(JAN, MAR_END, company=self.company, keys=MRP_KEYS)
        self.env.flush_all()
        return self.cr.sql_log_count - start

    def test_mrp_feeders_query_count_independent_of_orders(self):
        # AD-114: 15 more MOs (30 work orders) cost the same queries as the fixture
        budget = self._cost() + TOLERANCE
        for day in range(1, 16):
            self._done_mo(2.0, dt(3, day), dt(3, day, 20), (60.0, 24.0))
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            self.Fact._cmc_refresh_feeders(JAN, MAR_END, company=self.company, keys=MRP_KEYS)
            self.env.flush_all()
        self.assertEqual(self._facts("mrp_wo_minutes")[(dt(3, 1).date(), self.press)], 15 * 84.0)
        self.assertEqual(self._facts("mrp_mo_lead_minutes")[(dt(3, 1).date(), self.press)], 15 * 720.0)
