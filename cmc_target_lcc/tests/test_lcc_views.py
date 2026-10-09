"""WP-16 LCC-02 - one driver base, two views: the costing view (phase x category, LC unit cost) and the cash/NPV
view (years), both from the same lines incl. driver-based ones priced from the registry; alternatives against a
baseline (incremental flows and NPV). CONTRACT: see common.py."""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D_JAN, TlcCase


@tagged("post_install", "-at_install")
class TestLccViews(TlcCase):
    def _driver_lcc(self, **kw):
        # support in year 2 = 10 service hours x 100 (registry) = 1,000, plus 500 typed in year 1
        return self._lcc(
            [(1, 100, 30.0, False), (2, 100, 30.0, False)],
            [self._cost("Support y1", "use", "usual", 1, 500.0),
             {"name": "Support y2", "phase": "use", "category": "hidden", "year": 2, "holder_model": self.service._name,
              "holder_id": self.service.id, "driver_id": self.service_hours.id, "quantity": 10.0}],
            **{"rate_date": D_JAN, **kw},
        )

    def test_lcc02_driver_based_line_priced_from_registry(self):
        # LCC-02: a cost line driven by a measure is quantity x rate, snapshotted at Compute
        model = self._driver_lcc()
        model.action_compute()
        line = model.cost_line_ids.filtered(lambda c: c.driver_id)
        self.assertEqual(line.rate_value, 100.0)
        self.assertEqual(model.year_result_ids.filtered(lambda r: r.year == 2).cost, 1000.0)
        self.assertEqual(model.lc_cost, 1500.0)

    def test_lcc02_cost_view_and_cash_view_reconcile(self):
        # LCC-02 / INT-05: phase view, year view and header agree, undiscounted and discounted
        model = self._base_lcc(discount_rate=10.0)
        model.action_compute()
        self.assertAlmostEqual(sum(model.phase_result_ids.mapped("amount")), model.lc_cost, places=2)
        self.assertAlmostEqual(sum(model.year_result_ids.mapped("cost")), model.lc_cost, places=2)
        self.assertAlmostEqual(sum(model.phase_result_ids.mapped("pv_amount")), model.lc_cost_npv, places=2)
        self.assertAlmostEqual(sum(model.year_result_ids.mapped("pv_cost")), model.lc_cost_npv, places=2)
        self.assertAlmostEqual(model.lc_unit_cost, model.lc_cost / model.lifetime_volume, places=4)

    def test_lcc02_driver_rate_missing(self):
        # AD-108: a driver line without a published rate is not costed at zero
        model = self._driver_lcc(rate_date=D_JAN.replace(month=6))
        with self.assertRaises(UserError):
            model.action_compute()

    def test_lcc02_driver_rate_producer_locked(self):
        # AD-002: the producer of a driver rate stays computed while the model reads it
        model = self._driver_lcc()
        model.action_compute()
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()

    def test_lcc02_alternative_against_baseline(self):
        # LCC-02: alternative minus baseline per year and in NPV (0%): 500 better in year 3, 500 overall
        baseline = self._base_lcc()
        baseline.action_compute()
        alternative = self._base_lcc(baseline_id=baseline.id)
        alternative.cost_line_ids.filtered(lambda c: c.name == "Production y3").amount = 3500.0
        alternative.action_compute()
        rows = alternative.year_result_ids.sorted("year")
        self.assertEqual(rows.mapped("incremental_flow"), [0.0, 0.0, 0.0, 500.0])
        self.assertAlmostEqual(alternative.incremental_npv, 500.0, places=2)

    def test_lcc02_rn001_baseline_aligned_by_calendar_year(self):
        # RN-001: an alternative starting a year later is compared on the calendar year, not on the index
        baseline = self._lcc([(1, 1000, 10.0, False), (2, 1000, 10.0, False)], [self._cost("Launch", "rd", "usual", 0, 5000.0)],
                             start_year=2026, horizon_years=3, discount_rate=10.0)
        baseline.action_compute()
        self.assertEqual(baseline.year_result_ids.sorted("year").mapped("net_flow"), [-5000.0, 10000.0, 10000.0])
        alternative = self._lcc([(0, 1000, 10.0, False), (1, 1000, 10.0, False)], [], start_year=2027, horizon_years=2,
                                discount_rate=10.0, baseline_id=baseline.id)
        alternative.action_compute()
        rows = alternative.year_result_ids.sorted("year")
        self.assertEqual(rows.mapped("calendar_year"), [2027, 2028])
        # 2027: 10,000 - 10,000 = 0; 2028: 10,000 - 10,000 = 0 (index alignment would give 15,000 / 0)
        self.assertEqual(rows.mapped("incremental_flow"), [0.0, 0.0])
        # common timeline from 2026 at 10%: +5,000 (2026, baseline only) + 0/1.1 + 0/1.21 = 5,000
        # (index-based lc_profit_npv difference would be 19,090.91 - 12,355.37 = 6,735.54)
        self.assertAlmostEqual(alternative.incremental_npv, 5000.0, places=2)

    def test_lcc02_baseline_must_be_computed(self):
        # AD-002: a draft baseline is no certified comparison
        baseline = self._base_lcc()
        alternative = self._base_lcc(baseline_id=baseline.id)
        with self.assertRaises(UserError):
            alternative.action_compute()
