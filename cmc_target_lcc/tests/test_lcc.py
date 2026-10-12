"""WP-16 LCC-01 - life-cycle cost model per product/customer: phases (R&D .. take-back/disposal), horizon, yearly
volumes, cost categories, non-recurring kept separate, contingent liability as EMV, committed vs incurred, NPV
(incl. 0%, real vs nominal), LC unit cost, LC profit, cumulative cash and breakeven, residual recalculation when
actuals replace estimates. CONTRACT: see common.py."""

import psycopg2

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.cmc_base.models.cmc_utils import npv, real_rate

from .common import TlcCase

COSTS = [20000.0, 10500.0, 11000.0, 8000.0]
NETS = [-20000.0, 9500.0, 9000.0, 2000.0]


@tagged("post_install", "-at_install")
class TestLcc(TlcCase):
    def test_lcc01_lc_cost_unit_cost_and_profit(self):
        # LCC-01: LC cost 49,500 over 10,000 units = 4.95 per unit; revenue 50,000 -> LC profit 500
        model = self._base_lcc()
        model.action_compute()
        self.assertRecordValues(model, [{
            "state": "done", "lc_cost": 49500.0, "non_recurring_cost": 20000.0, "recurring_cost": 29500.0,
            "lifetime_volume": 10000.0, "lc_unit_cost": 4.95, "lc_revenue": 50000.0, "lc_profit": 500.0,
            "effective_rate": 0.0, "lc_cost_npv": 49500.0, "lc_profit_npv": 500.0,
        }])

    def test_lcc01_turney_unit_cost(self):
        # Turney Ch8: an LC account of $20,000 over a lifetime of 10,000 units = $2 per unit
        model = self._lcc([(1, 2500, 0.0, False), (2, 2500, 0.0, False), (3, 2500, 0.0, False), (4, 2500, 0.0, False)],
                          [self._cost("Development", "rd", "usual", 0, 20000.0, recurrence="non_recurring")], horizon_years=5)
        model.action_compute()
        self.assertRecordValues(model, [{"lc_unit_cost": 2.0, "residual_unit_cost": 2.0, "absorbed_to_date": 0.0,
                                         "has_actuals": False}])

    def test_lcc01_yearly_cash_view_and_breakeven(self):
        # LCC-01: net -20,000 / 9,500 / 9,000 / 2,000 -> cumulative -20,000 / -10,500 / -1,500 / 500, breakeven 2029
        model = self._base_lcc()
        model.action_compute()
        rows = model.year_result_ids.sorted("year")
        self.assertEqual(rows.mapped("year"), [0, 1, 2, 3])
        self.assertEqual(rows.mapped("calendar_year"), [2026, 2027, 2028, 2029])
        self.assertEqual(rows.mapped("cost"), COSTS)
        self.assertEqual(rows.mapped("revenue"), [0.0, 20000.0, 20000.0, 10000.0])
        self.assertEqual(rows.mapped("net_flow"), NETS)
        self.assertEqual(rows.mapped("cumulative_net"), [-20000.0, -10500.0, -1500.0, 500.0])
        self.assertEqual(rows.mapped("non_recurring_cost"), [20000.0, 0.0, 0.0, 0.0])
        self.assertRecordValues(model, [{"has_breakeven": True, "breakeven_year": 2029}])

    def test_lcc01_no_breakeven(self):
        # LCC-01: a life that never recovers its outlay has no breakeven year
        model = self._lcc([(1, 100, 1.0, False)], [self._cost("R&D", "rd", "usual", 0, 5000.0)])
        model.action_compute()
        self.assertFalse(model.has_breakeven)
        self.assertEqual(model.lc_profit, -4900.0)

    def test_lcc01_contingent_liability_as_emv(self):
        # Emblemsvag Ch5-6: a take-back/disposal liability of 10,000 at 20% weighs its EMV 2,000
        model = self._base_lcc()
        model.action_compute()
        liability = model.phase_result_ids.filtered(lambda r: r.phase == "take_back")
        self.assertRecordValues(liability, [{"category": "liability", "amount": 2000.0}])

    def test_lcc01_phases_and_categories(self):
        # LCC-01: cost view by phase x category (usual, hidden, liability, less tangible) and its share of LC cost
        model = self._base_lcc()
        model.action_compute()
        view = {(r.phase, r.category): (r.amount, r.share_pct) for r in model.phase_result_ids}
        self.assertEqual(view, {
            ("rd", "usual"): (20000.0, 40.40),
            ("production", "usual"): (20000.0, 40.40),
            ("distribution", "usual"): (5000.0, 10.10),
            ("distribution", "less_tangible"): (500.0, 1.01),
            ("use", "hidden"): (2000.0, 4.04),
            ("take_back", "liability"): (2000.0, 4.04),
        })

    def test_lcc01_committed_vs_incurred(self):
        # LCC-01 / Turney Ch1: 95.96% of the LC cost is committed in year 0 while 40.40% is incurred
        model = self._base_lcc()
        model.action_compute()
        rows = model.year_result_ids.sorted("year")
        self.assertEqual(rows.mapped("committed_cost"), [47500.0, 2000.0, 0.0, 0.0])
        self.assertEqual(rows.mapped("cumulative_committed_pct"), [95.96, 100.0, 100.0, 100.0])
        self.assertEqual(rows.mapped("cumulative_incurred_pct"), [40.40, 61.62, 83.84, 100.0])

    def test_lcc01_npv_at_real_rate(self):
        # LCC-01 / AD-119: 10% real on real flows -> PV cost 44,646.88, NPV of the net flows -2,422.99
        model = self._base_lcc(discount_rate=10.0)
        model.action_compute()
        self.assertAlmostEqual(model.lc_cost_npv, 44646.88, places=2)
        self.assertAlmostEqual(model.lc_profit_npv, -2422.99, places=2)
        rows = model.year_result_ids.sorted("year")
        self.assertAlmostEqual(rows[2].discount_factor, 1 / 1.21, places=6)
        self.assertAlmostEqual(rows[1].pv_cost, 9545.45, places=2)
        self.assertAlmostEqual(rows[3].cumulative_pv, -2422.99, places=2)

    def test_lcc01_real_flows_nominal_rate(self):
        # Emblemsvag Ch5: real flows + 10% nominal at 2% inflation -> discount at 7.843137% real (exact Fisher)
        model = self._base_lcc(discount_rate=10.0, rate_basis="nominal", inflation_rate=2.0)
        model.action_compute()
        rate = real_rate(0.10, 0.02)
        self.assertAlmostEqual(model.effective_rate, rate * 100, places=6)
        self.assertAlmostEqual(model.lc_cost_npv, npv(rate, COSTS), places=2)
        # same as inflating the flows to nominal and discounting at the nominal rate
        self.assertAlmostEqual(model.lc_cost_npv, sum(c * 1.02**t / 1.1**t for t, c in enumerate(COSTS)), places=2)

    def test_lcc01_nominal_flows_real_rate(self):
        # AD-119: nominal flows + real rate -> nominal_rate(5%, 2%) = 7.1%
        model = self._base_lcc(discount_rate=5.0, flows_basis="nominal", inflation_rate=2.0)
        model.action_compute()
        self.assertAlmostEqual(model.effective_rate, 7.1, places=6)
        self.assertAlmostEqual(model.lc_profit_npv, npv(0.071, NETS), places=2)

    def test_lcc01_actuals_replace_estimates(self):
        # LCC-01: an actual year uses the typed actual (production 8,600 instead of 8,000; liability ignored)
        years = [(0, 0, 0.0, False), (1, 4000, 5.0, True)]
        costs = [
            self._cost("Production y1", "production", "usual", 1, 8000.0, actual_amount=8600.0),
            self._cost("Liability y1", "take_back", "liability", 1, 10000.0, probability_pct=20.0, actual_amount=0.0,
                       actual_zero=True),
            self._cost("Production y2", "production", "usual", 2, 8000.0),
        ]
        model = self._lcc(years, costs)
        model.action_compute()
        rows = model.year_result_ids.sorted("year")
        self.assertEqual(rows.mapped("cost"), [0.0, 8600.0, 8000.0, 0.0])
        self.assertEqual(rows.mapped("is_actual"), [False, True, False, False])
        self.assertTrue(model.has_actuals)

    def test_lcc01_residual_recalculation(self):
        # Turney Ch8/14: 3,000 units absorbed at $2 = 6,000; residual 14,000 / revised 5,000 units = $2.80
        estimate = self._lcc([(1, 2500, 0.0, False), (2, 2500, 0.0, False), (3, 2500, 0.0, False), (4, 2500, 0.0, False)],
                             [self._cost("Development", "rd", "usual", 0, 20000.0, recurrence="non_recurring")], horizon_years=5)
        estimate.action_compute()
        revised = self._lcc(
            [(0, 0, 0.0, True), (1, 3000, 0.0, True), (2, 2500, 0.0, False), (3, 2500, 0.0, False)],
            [self._cost("Development", "rd", "usual", 0, 20000.0, recurrence="non_recurring", actual_amount=20000.0)],
            horizon_years=5, previous_id=estimate.id,
        )
        revised.action_compute()
        self.assertRecordValues(revised, [{
            "lc_cost": 20000.0, "lifetime_volume": 8000.0, "lc_unit_cost": 2.5, "absorbed_to_date": 6000.0,
            "residual_cost": 14000.0, "residual_volume": 5000.0, "residual_unit_cost": 2.8,
        }])

    def test_lcc01_rn001_residual_aligned_by_calendar_year(self):
        # RN-001: a revision starting in 2028 absorbs 2028 at the rate in force of a (2.80), matched on the calendar year
        estimate = self._lcc([(1, 2500, 0.0, False), (2, 2500, 0.0, False), (3, 2500, 0.0, False), (4, 2500, 0.0, False)],
                             [self._cost("Development", "rd", "usual", 0, 20000.0, recurrence="non_recurring")], horizon_years=5)
        estimate.action_compute()
        model_a = self._lcc(
            [(0, 0, 0.0, True), (1, 3000, 0.0, True), (2, 2500, 0.0, False), (3, 2500, 0.0, False)],
            [self._cost("Development", "rd", "usual", 0, 20000.0, recurrence="non_recurring", actual_amount=20000.0)],
            horizon_years=5, previous_id=estimate.id,
        )
        model_a.action_compute()
        self.assertRecordValues(model_a, [{"absorbed_to_date": 6000.0, "residual_unit_cost": 2.8}])
        # c starts 2028: its year 0 (2028) is NOT actual in a (a's 2028 is its year 2); index alignment would see
        # year 0 as already actual in a and absorb nothing (6,000 / residual 5.60)
        revision = self._lcc(
            [(0, 2500, 0.0, True), (1, 2500, 0.0, False)],
            # the life cost of the account to date, carried into the revision's year 0 (whole-life lc_cost)
            [self._cost("Development to date", "rd", "usual", 0, 20000.0, recurrence="non_recurring", actual_amount=20000.0)],
            start_year=2028, horizon_years=2, previous_id=model_a.id,
        )
        revision.action_compute()
        self.assertRecordValues(revision, [{
            "lc_cost": 20000.0, "absorbed_to_date": 13000.0, "residual_cost": 7000.0, "residual_volume": 2500.0,
            "residual_unit_cost": 2.8,
        }])

    def test_lcc01_rn002_actual_not_typed_raises(self):
        # RN-002: in an actual year an estimated cost with actual 0 and no confirmation is a missing actual, not a zero
        years = [(0, 0, 0.0, False), (1, 4000, 5.0, True)]
        costs = [
            self._cost("Production y1", "production", "usual", 1, 8000.0, actual_amount=8600.0),
            self._cost("Warranty y1 untyped", "use", "hidden", 1, 1000.0),
        ]
        model = self._lcc(years, costs)
        with self.assertRaisesRegex(UserError, "Warranty y1 untyped"):
            model.action_compute()

    def test_lcc01_rn002_confirmed_zero_accepted(self):
        # RN-002: the explicit actual_zero flag certifies that the estimated cost really did not happen
        years = [(1, 4000, 5.0, True)]
        costs = [self._cost("Warranty y1", "use", "hidden", 1, 1000.0, actual_zero=True),
                 self._cost("No estimate y1", "use", "usual", 1, 0.0)]
        model = self._lcc(years, costs)
        model.action_compute()
        self.assertRecordValues(model, [{"state": "done", "lc_cost": 0.0, "has_actuals": True}])

    def test_lcc01_previous_model_locked_while_read(self):
        # AD-002: the model whose unit cost was in force cannot be reset while a revision reads it
        estimate = self._lcc([(1, 100, 0.0, False)], [self._cost("R&D", "rd", "usual", 0, 100.0)])
        estimate.action_compute()
        revised = self._lcc([(1, 100, 0.0, True)], [self._cost("R&D", "rd", "usual", 0, 100.0)], previous_id=estimate.id)
        revised.action_compute()
        with self.assertRaises(UserError):
            estimate.action_reset_to_draft()

    def test_lcc01_customer_lifetime_profit(self):
        # LCC-01: lifetime profit of a customer (no product)
        model = self._lcc([(1, 10, 100.0, False)], [self._cost("Service", "use", "usual", 1, 400.0)],
                          product_id=False, partner_id=self.customer.id)
        model.action_compute()
        self.assertEqual(model.lc_profit, 600.0)

    def test_lcc01_header_constraints(self):
        # LCC-01: a model has an object (product or customer) and a horizon of at least one year
        with self.assertRaises(ValidationError):
            self._lcc([], [], product_id=False)
        with self.assertRaises(ValidationError):
            self._lcc([], [], horizon_years=0)

    def test_lcc01_line_outside_horizon(self):
        # LCC-01: a cost dated after the horizon is not silently dropped
        model = self._lcc([], [self._cost("Late", "use", "usual", 9, 100.0)])
        with self.assertRaises(UserError):
            model.action_compute()

    def test_lcc01_one_line_per_year(self):
        # LCC-01: two volume lines for the same year are ambiguous
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError):
            self._lcc([(1, 10, 1.0, False), (1, 20, 1.0, False)], [])
            self.env.flush_all()

    def test_lcc01_locked_and_reset(self):
        # AD-113: a computed model keeps its inputs; reset deletes both views and clears the outputs
        model = self._base_lcc()
        model.action_compute()
        with self.assertRaises(UserError):
            model.cost_line_ids[:1].amount = 1.0
        with self.assertRaises(UserError):
            model.horizon_years = 10
        model.action_reset_to_draft()
        self.assertFalse(model.year_result_ids)
        self.assertFalse(model.phase_result_ids)
        self.assertRecordValues(model, [{"state": "draft", "lc_cost": 0.0, "lc_unit_cost": 0.0}])
