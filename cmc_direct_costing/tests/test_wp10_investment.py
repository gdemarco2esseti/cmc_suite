"""WP-10 DIF-06 - investment / replacement appraisal over a horizon with a cash-flow table.

CONTRACT (part of wp10_common; decision_type ``investment``; maths through cmc_utils, AD-119)
- Inputs: ``cash_flow_ids`` (``year`` >= 0, ``amount``, ``depreciation``, ``taxable`` Boolean default True
  (RN-002: False for capex / working capital / salvage at book value); ``models.UniqueIndex`` on
  (decision_id, year, taxable) -> IntegrityError on a second line of the same year AND kind, while one
  taxable (operating) + one non-taxable (capital) line per year is allowed), ``discount_rate`` /
  ``inflation_rate`` / ``tax_rate`` percent, ``rate_basis`` / ``flows_basis`` ``nominal`` | ``real``.
- Tax (years >= 1): ``tax`` = tax_rate x (Σ amount of the TAXABLE lines - Σ depreciation of the year)
  (negative = tax saving); a non-taxable amount is never in the base (a mid-life capex is no tax credit,
  a salvage at book value is no taxable gain); ``net_flow`` = Σ amount of all lines - tax; year 0 untaxed.
  The result row ``amount`` = Σ amount of the year (pre-tax).
- Rate consistent with the flows (Fisher, exact): same basis -> discount_rate; real flows + nominal rate
  -> real_rate(rate, inflation); nominal flows + real rate -> nominal_rate(rate, inflation); stored as
  ``effective_rate`` (percent).
- One ``cmc.decision.cash.flow.result`` per year (ordered by year): ``discount_factor`` = 1/(1+r)^t,
  ``present_value``, ``cumulative_flow``, ``cumulative_pv``.
- Outputs: ``npv`` = Σ PV of net flows; ``irr`` percent (``has_irr`` False and 0 when no sign change);
  RN-002: more than one sign change -> ``has_irr`` False, ``irr`` 0 and ``irr_note`` Char (readonly, an
  output cleared by reset) containing "multiple IRR" (cmc_utils.irr raises ``MultipleIrrError``);
  ``irr_note`` False when the IRR is stated;
  ``payback_years`` / ``discounted_payback_years`` (``has_*`` False when never recovered; RN-002: also
  False when the year-0 net flow is >= 0 - no investment at t = 0, nothing to pay back -, never 0 years); ``roi`` =
  (Σ net flows of years >= 1 - I) / I x 100 with I = -(year-0 net flow); ``cbr`` = PV of positive net flows
  / |PV of negative net flows|; verdict alternative (invest) when npv > 0, current when < 0.
"""

import psycopg2

from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .wp10_common import Wp10Case


@tagged("post_install", "-at_install")
class TestWp10Investment(Wp10Case):
    def _investment(self, flows, **kw):
        # flows: (year, amount, depreciation) or (year, amount, depreciation, taxable)
        cash = [(0, 0, {"year": f[0], "amount": f[1], "depreciation": f[2], **({"taxable": f[3]} if len(f) > 3 else {})})
                for f in flows]
        return self._decision("investment", cash_flow_ids=cash, **kw)

    def test_npv_irr_payback(self):
        # DIF-06: -1000, 600, 600 at 10% -> NPV 41.32, IRR 13.07%, payback 1.67, discounted 1.92, ROI 20%, CBR 1.0413
        decision = self._investment([(0, -1000, 0), (1, 600, 0), (2, 600, 0)], discount_rate=10.0)
        decision.action_compute()
        self.assertAlmostEqual(decision.npv, 41.32, places=2)
        self.assertTrue(decision.has_irr)
        self.assertAlmostEqual(decision.irr, 13.0662, places=3)
        self.assertAlmostEqual(decision.payback_years, 1.0 + 400.0 / 600.0, places=4)
        self.assertAlmostEqual(decision.discounted_payback_years, 1.0 + 11.0 / 12.0, places=4)
        self.assertAlmostEqual(decision.roi, 20.0, places=4)
        self.assertAlmostEqual(decision.cbr, 1.0413, places=4)
        self.assertEqual(decision.verdict, "alternative")

    def test_cash_flow_table(self):
        # DIF-06: the per-year table behind the NPV (factor, PV, cumulative)
        decision = self._investment([(0, -1000, 0), (1, 600, 0), (2, 600, 0)], discount_rate=10.0)
        decision.action_compute()
        rows = decision.cash_flow_result_ids.sorted("year")
        self.assertEqual(rows.mapped("year"), [0, 1, 2])
        for row, cumulative in zip(rows, [-1000.0, -400.0, 200.0], strict=True):
            self.assertAlmostEqual(row.cumulative_flow, cumulative, places=2)
        self.assertAlmostEqual(rows[1].discount_factor, 1 / 1.1, places=6)
        self.assertAlmostEqual(rows[2].present_value, 495.87, places=2)
        self.assertAlmostEqual(rows[2].cumulative_pv, 41.32, places=2)

    def test_zero_rate(self):
        # DIF-06 / LCC: r = 0 is allowed, NPV = plain sum
        decision = self._investment([(0, -1000, 0), (1, 600, 0), (2, 600, 0)], discount_rate=0.0)
        decision.action_compute()
        self.assertAlmostEqual(decision.npv, 200.0, places=2)
        self.assertAlmostEqual(decision.discounted_payback_years, decision.payback_years, places=6)

    def test_tax_turns_the_verdict(self):
        # DIF-06: tax 30% x (600 - 500 depreciation) = 30 -> 570/yr -> NPV -10.74 -> do not invest
        decision = self._investment([(0, -1000, 0), (1, 600, 500), (2, 600, 500)], discount_rate=10.0, tax_rate=30.0)
        decision.action_compute()
        rows = decision.cash_flow_result_ids.sorted("year")
        for row, tax, net in zip(rows, [0.0, 30.0, 30.0], [-1000.0, 570.0, 570.0], strict=True):
            self.assertAlmostEqual(row.tax, tax, places=2)
            self.assertAlmostEqual(row.net_flow, net, places=2)
        self.assertAlmostEqual(decision.npv, -10.74, places=2)
        self.assertEqual(decision.verdict, "current")

    def test_real_flows_nominal_rate(self):
        # DIF-06 / LCC Ch.5: real flows discounted at the real rate (1.155/1.05 - 1 = 10%) -> 41.32
        decision = self._investment([(0, -1000, 0), (1, 600, 0), (2, 600, 0)], discount_rate=15.5,
                                    inflation_rate=5.0, flows_basis="real", rate_basis="nominal")
        decision.action_compute()
        self.assertAlmostEqual(decision.effective_rate, 10.0, places=6)
        self.assertAlmostEqual(decision.npv, 41.32, places=2)

    def test_nominal_flows_real_rate(self):
        # DIF-06: inflated flows 630 / 661.5 at real 10% + inflation 5% = nominal 15.5% -> the same 41.32
        decision = self._investment([(0, -1000, 0), (1, 630, 0), (2, 661.5, 0)], discount_rate=10.0,
                                    inflation_rate=5.0, flows_basis="nominal", rate_basis="real")
        decision.action_compute()
        self.assertAlmostEqual(decision.effective_rate, 15.5, places=6)
        self.assertAlmostEqual(decision.npv, 41.32, places=2)

    def test_never_recovered(self):
        # DIF-06: no payback and no IRR are stated as such, not as 0 years / 0%
        decision = self._investment([(0, -1000, 0), (1, 300, 0), (2, 300, 0)], discount_rate=10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"has_payback": False, "has_discounted_payback": False, "verdict": "current"}])
        no_outflow = self._investment([(0, 100, 0), (1, 50, 0)], discount_rate=10.0)
        no_outflow.action_compute()
        self.assertRecordValues(no_outflow, [{"has_irr": False, "irr": 0.0}])

    def test_one_flow_per_year(self):
        # DIF-06: two lines for the same year would double the flow silently
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self._investment([(1, 600, 0), (1, 100, 0)], discount_rate=10.0)
            self.env.flush_all()

    # ---- RN-002 -----------------------------------------------------------------------

    def test_taxable_defaults_true(self):
        # RN-002: an operating flow is taxed unless the user says otherwise
        decision = self._investment([(0, -1000, 0), (1, 600, 0)])
        self.assertEqual(decision.cash_flow_ids.mapped("taxable"), [True, True])

    def test_capital_flows_are_not_taxed(self):
        # RN-002: t 30%, r 10%. y1 600 dep 400 -> tax 60, net 540; y2 600 dep 400 + capex -400 (not taxable)
        # -> tax 60 (not -60), net 140; y3 600 dep 400 + salvage 200 at book value (not taxable) -> tax 60
        # (not 120), net 740. NPV = -1000 + 540/1.1 + 140/1.21 + 740/1.331 = 162.58
        decision = self._investment([
            (0, -1000, 0, False),
            (1, 600, 400),
            (2, 600, 400), (2, -400, 0, False),
            (3, 600, 400), (3, 200, 0, False),
        ], discount_rate=10.0, tax_rate=30.0)
        decision.action_compute()
        rows = decision.cash_flow_result_ids.sorted("year")
        self.assertEqual(rows.mapped("year"), [0, 1, 2, 3])
        for row, amount, tax, net in zip(rows, [-1000.0, 600.0, 200.0, 800.0], [0.0, 60.0, 60.0, 60.0],
                                         [-1000.0, 540.0, 140.0, 740.0], strict=True):
            self.assertAlmostEqual(row.amount, amount, places=2)
            self.assertAlmostEqual(row.tax, tax, places=2)
            self.assertAlmostEqual(row.net_flow, net, places=2)
        self.assertAlmostEqual(decision.npv, 162.58, places=2)

    def test_no_investment_at_year_zero_has_no_payback(self):
        # RN-002: a deferred outflow (0, -1000, +600, +600) is not "paid back in 0 years"
        decision = self._investment([(0, 0, 0), (1, -1000, 0), (2, 600, 0), (3, 600, 0)], discount_rate=10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"has_payback": False, "payback_years": 0.0,
                                            "has_discounted_payback": False, "discounted_payback_years": 0.0,
                                            "has_irr": True, "irr_note": False}])
        self.assertAlmostEqual(decision.irr, 13.0662, places=3)  # same IRR, just shifted one year
        inflow_only = self._investment([(0, 100, 0), (1, 50, 0)], discount_rate=10.0)
        inflow_only.action_compute()
        self.assertRecordValues(inflow_only, [{"has_payback": False, "has_discounted_payback": False}])

    def test_multiple_irr_is_not_stated(self):
        # RN-002: -100, +230, -132 has two IRRs (10% and 20%): neither is reported, the note says why
        decision = self._investment([(0, -100, 0), (1, 230, 0), (2, -132, 0)], discount_rate=15.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"has_irr": False, "irr": 0.0}])
        self.assertIn("multiple irr", (decision.irr_note or "").lower())
        decision.action_reset_to_draft()
        self.assertFalse(decision.irr_note)

    def test_conventional_flows_have_no_irr_note(self):
        # RN-002: the note only appears when the IRR is withheld
        decision = self._investment([(0, -1000, 0), (1, 600, 0), (2, 600, 0)], discount_rate=10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"has_irr": True, "irr_note": False}])
