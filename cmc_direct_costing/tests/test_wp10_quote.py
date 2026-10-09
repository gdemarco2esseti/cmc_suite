"""WP-10 DIF-04 - cost an order / quote from activity rates and test it against the target margin.

CONTRACT (part of wp10_common; decision_type ``special_order``)
- ``activity_cost`` = Σ activity lines quantity x rate (``cmc.rate._cmc_lookup`` of holder x driver x month
  of ``date`` in the decision company; snapshot in ``rate_value`` / ``amount``).
- Burdens are decision-dependent (Cokins Ch.8): ``idle_burden`` = activity_cost x ``idle_burden_pct`` / 100
  only when ``include_idle``, ``sustaining_burden`` likewise with ``include_sustaining``; else 0.
- ``quote_cost`` = Σ cost lines ``amount_alternative`` (the order's own costs) + activity_cost + burdens +
  ``capital_charge``; ``quote_margin`` = ``quote_price`` - quote_cost; ``quote_margin_pct`` = margin /
  price x 100 (0 without price); ``differential`` = quote_margin.
- ``verdict`` ``alternative`` (accept, label "Accept order") when quote_margin_pct >= target_margin_pct
  (at 0.01) and margin > 0, else ``current`` (label "Reject order").
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp10_common import Wp10Case


@tagged("post_install", "-at_install")
class TestWp10Quote(Wp10Case):
    def _quote(self, **kw):
        vals = {
            "quote_price": 10000.0,
            "target_margin_pct": 20.0,
            "capital_charge": 200.0,
            "idle_burden_pct": 10.0,
            "sustaining_burden_pct": 20.0,
            "line_ids": [
                self._line("Materials", "cost", 0, 3000),
                self._line("Outsourced finishing", "cost", 0, 1000),
            ],
            "activity_ids": [
                self._act(self.c_machining, self.mh, 20),
                self._act(self.c_setup, self.setups, 4),
            ],
        }
        vals.update(kw)
        return self._decision("special_order", **vals)

    def test_quote_cost_build_up_without_burdens(self):
        # DIF-04: 4,000 own + 20x120 + 4x150 = 3,000 activities + 200 capital = 7,200 -> 28% >= 20% -> accept
        quote = self._quote()
        quote.action_compute()
        self.assertRecordValues(quote, [{
            "activity_cost": 3000.0, "idle_burden": 0.0, "sustaining_burden": 0.0, "quote_cost": 7200.0,
            "quote_margin": 2800.0, "quote_margin_pct": 28.0, "verdict": "alternative", "verdict_label": "Accept order",
        }])
        self.assertRecordValues(quote.activity_ids.sorted("id"), [
            {"rate_value": 120.0, "amount": 2400.0},
            {"rate_value": 150.0, "amount": 600.0},
        ])

    def test_including_idle_and_sustaining_rejects(self):
        # DIF-04 / Cokins Ch.8: +300 idle +600 sustaining -> 8,100, margin 19% < 20% -> reject
        quote = self._quote(include_idle=True, include_sustaining=True)
        quote.action_compute()
        self.assertRecordValues(quote, [{
            "idle_burden": 300.0, "sustaining_burden": 600.0, "quote_cost": 8100.0, "quote_margin": 1900.0,
            "quote_margin_pct": 19.0, "verdict": "current", "verdict_label": "Reject order",
        }])

    def test_only_sustaining_included(self):
        # DIF-04: the option is per burden: 7,200 + 600 = 7,800 -> 22% -> accept
        quote = self._quote(include_sustaining=True)
        quote.action_compute()
        self.assertRecordValues(quote, [{"idle_burden": 0.0, "quote_cost": 7800.0, "quote_margin_pct": 22.0, "verdict": "alternative"}])

    def test_margin_exactly_on_target_accepts(self):
        # DIF-04: the threshold is inclusive: price 9,000 -> 1,800 / 9,000 = 20%
        quote = self._quote(quote_price=9000.0)
        quote.action_compute()
        self.assertRecordValues(quote, [{"quote_margin": 1800.0, "quote_margin_pct": 20.0, "verdict": "alternative"}])

    def test_rates_of_another_month_are_not_used(self):
        # DIF-04 / AD-108: the rate is the one of the decision month, never a neighbour
        quote = self._quote(date="2026-03-10")
        with self.assertRaises(UserError):
            quote.action_compute()

    def test_quote_rates_are_a_snapshot(self):
        # DIF-04 / AD-113: the computed quote keeps the rate it priced with
        quote = self._quote()
        quote.action_compute()
        self.assertEqual(quote.activity_ids.mapped("rate_value"), [120.0, 150.0])
        with self.assertRaises(UserError):
            quote.activity_ids[:1].quantity = 99.0
