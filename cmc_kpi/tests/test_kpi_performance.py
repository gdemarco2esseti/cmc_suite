"""AD-114 cost of access: the KPI run, the pack and the feeders read grouped, whatever the volume.

Volume fixtures: 40 measure KPIs x 3 months over 1,200 facts vs 4 KPIs over 12 facts; 30 extra validated
deliveries vs none. A per-KPI or per-fact query (N+1) makes the big case cost more than the small one + TOLERANCE.
"""

from datetime import date

from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiCase, dt

TOLERANCE = 3


@tagged("post_install", "-at_install")
class TestKpiPerformance(KpiCase):
    def _volume(self, n_kpis, facts_per_kpi_month, tag):
        kpis = self.env["cmc.kpi"]
        Driver = self.env["cmc.cost.driver"]
        facts = []
        for i in range(n_kpis):
            driver = Driver.create({"name": "vol %s %s" % (tag, i), "code": "VOL%s%s" % (tag, i), "measure_kind": "statistical"})
            kpis |= self._kpi("vol_%s_%s" % (tag, i), "measure", driver_id=driver.id, target_value=10.0,
                              aggregation="avg" if i % 2 else "sum")
            facts += [{"driver_id": driver.id, "date": month, "quantity": 1.0 + j, "company_id": self.company.id}
                      for month in (JAN, FEB, MAR) for j in range(facts_per_kpi_month)]
        self.env["cmc.measure.quantity"].create(facts)
        formula = self._kpi("vol_%s_formula" % tag, "formula", formula="vol_%s_0 + vol_%s_1" % (tag, tag))
        return kpis | formula

    def _cost_of(self, run):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        run.action_compute()
        self.env.flush_all()
        return self.cr.sql_log_count - start

    def test_run_query_count_independent_of_kpis_and_facts(self):
        # AD-114: a monthly library of 40 KPIs over 1,200 facts costs what 4 KPIs over 12 facts cost
        small = self._volume(4, 1, "s")
        big = self._volume(40, 10, "b")
        run_small = self._run(JAN, MAR, compute=False, kpi_ids=[(6, 0, small.ids)])
        run_big = self._run(JAN, MAR, compute=False, kpi_ids=[(6, 0, big.ids)])
        budget = self._cost_of(run_small) + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            run_big.action_compute()
            self.env.flush_all()
        self.assertEqual(len(run_big.value_ids), 41 * 3)
        # sum KPI vol_b_0: 10 facts 1..10 = 55 per month; avg KPI vol_b_1: 5.5; formula 60.5
        by_code = {row.kpi_id.code: row.value for row in run_big.value_ids if row.date == JAN}
        self.assertEqual((by_code["vol_b_0"], by_code["vol_b_1"], by_code["vol_b_formula"]), (55.0, 5.5, 60.5))

    def test_feeder_query_count_independent_of_pickings(self):
        # AD-107 / AD-114: the OTD and scrap feeders group in SQL: 30 more deliveries cost no more queries
        Qty = self.env["cmc.measure.quantity"]
        keys = ["stock_otd_shipped", "stock_otd_on_time", "stock_scrap_qty"]
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        Qty._cmc_refresh_feeders(JAN, date(2026, 3, 31), company=self.company, keys=keys)
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self._ship([(dt(1, 28), dt(1, day % 28 + 1)) for day in range(30)])
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            Qty._cmc_refresh_feeders(JAN, date(2026, 3, 31), company=self.company, keys=keys)
            self.env.flush_all()
        shipped = Qty.search([("feeder_key", "=", "stock_otd_shipped"), ("date", "=", JAN)])
        self.assertEqual(sum(shipped.mapped("quantity")), 34.0)

    def test_pack_query_count_independent_of_kpis(self):
        # AD-114: the pack reads the run rows grouped: 40 KPIs cost what 4 cost
        small = self._volume(4, 1, "ps")
        big = self._volume(40, 2, "pb")
        run_small = self._run(JAN, kpi_ids=[(6, 0, small.ids)], bva_version_id=self.budget.id, fin_analysis_id=self.fin.id)
        run_big = self._run(JAN, kpi_ids=[(6, 0, big.ids)], bva_version_id=self.budget.id, fin_analysis_id=self.fin.id)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        run_small._cmc_pack_data()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            data = run_big._cmc_pack_data()
        self.assertEqual(len(data["kpis"]), 41)
