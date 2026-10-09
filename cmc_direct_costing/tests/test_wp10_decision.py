"""WP-10 DIF-01 / DIF-03 - persisted differential decisions (current vs alternative columns).

CONTRACT (part of wp10_common)
- ``cmc.decision`` is a run: ``action_compute`` from draft -> state ``done``; a computed decision refuses
  edits of header inputs and lines (UserError); ``action_reset_to_draft`` clears the outputs.
- Lines: ``avoidable`` / ``sunk`` follow the cost element flags (CLS-04), overridable on the line; sunk lines
  are never relevant; ``relevant`` = not sunk and amount_current != amount_alternative.
- generic / replace: column profit (``current_total`` / ``alternative_total``) = Σ revenue - Σ cost over the
  NON-SUNK lines, minus the activity amounts of the side (equal lines cancel in the difference);
  ``differential`` = alternative - current; ``verdict`` alternative when > 0, current when < 0,
  indifferent at 0 (currency step).
- drop_line: the alternative column is DERIVED (the typed one is ignored): revenue -> 0, avoidable cost
  -> 0, unavoidable cost -> as current (Cinquini III.4.1: Δ = -(1st MC) + avoidable fixed).
- make_or_buy (current = make, alternative = buy): alternative derived as in drop_line plus
  ``buy_price_unit`` x ``volume``; ``opportunity_cost`` added to make; hidden activity costs
  (``activity_ids``, qty x rate looked up at Compute) added to their side. Indifference
  Q* = (CFr + OC + A_make - A_buy) / (Pe - v), v = Σ variable make cost lines / volume, CFr = Σ fixed
  avoidable make cost lines, OC = ``opportunity_cost`` (RN-001: a fixed burden of the make side, so the
  verdict and Q* always agree: volume < Q* <=> Buy), A_side = activity cost of the side (treated as fixed);
  ``has_indifference`` False and Q* 0 when Pe <= v. ``verdict_label``: "Make" (current) / "Buy" (alternative).
- RN-001: a ``cost_behavior`` = ``variable`` cost line is ALWAYS avoidable in the derived alternative
  column (drop_line and make_or_buy), whatever its element / ``avoidable`` flag: producing nothing incurs no
  variable cost. ``avoidable`` only discriminates the FIXED lines (derivation and CFr alike).
- Wording of ``verdict_label`` per type (alternative / current): make_or_buy "Buy" / "Make", drop_line
  "Eliminate line" / "Keep line", replace "Replace" / "Keep machine"; "Indifferent" at 0 (currency step,
  so rounding noise is never a verdict). These scenarios replace the removed transient
  ``cmc.differential.analysis`` (same numbers, sign convention differential = alternative - current).
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp10_common import Wp10Case


@tagged("post_install", "-at_install")
class TestWp10Decision(Wp10Case):
    # ---- DIF-01 generic and persisted -------------------------------------------------

    def _split_off(self):
        return self._decision(
            "generic",
            line_ids=[
                self._line("Revenue", "revenue", 50000, 80000),
                self._line("Further processing", "cost", 0, 22000, element_id=self.el_avoidable.id),
                self._line("Joint cost", "cost", 40000, 40000, element_id=self.el_fixed.id),
            ],
        )

    def test_generic_split_off_further_processing(self):
        # DIF-01 / Cinquini IV.3.2: +30,000 revenue - 22,000 extra cost = +8,000, joint cost irrelevant
        decision = self._split_off()
        decision.action_compute()
        self.assertRecordValues(decision, [{"state": "done", "current_total": 10000.0, "alternative_total": 18000.0,
                                            "differential": 8000.0, "verdict": "alternative"}])
        joint = decision.line_ids.filtered(lambda line: line.name == "Joint cost")
        self.assertFalse(joint.relevant)

    def test_lines_follow_element_flags(self):
        # DIF-01 / CLS-04: avoidable and sunk come from the cost element, editable per line
        decision = self._decision(
            "generic",
            line_ids=[
                self._line("avoid", "cost", 10, 0, element_id=self.el_avoidable.id),
                self._line("sunk", "cost", 0, 10, element_id=self.el_sunk.id),
                self._line("free", "cost", 10, 0),
            ],
        )
        lines = decision.line_ids.sorted("id")
        self.assertRecordValues(lines, [
            {"avoidable": True, "sunk": False, "relevant": True},
            {"avoidable": False, "sunk": True, "relevant": False},
            {"avoidable": False, "sunk": False, "relevant": True},
        ])
        lines[2].avoidable = True
        self.assertTrue(lines[2].avoidable)

    def test_replace_ignores_book_value(self):
        # DIF-01 / Cinquini III.4.3: replacement wins by 120k; the write-off of the old book value is sunk
        decision = self._decision(
            "replace",
            line_ids=[
                self._line("Operating costs 3y", "cost", 300000, 60000),
                self._line("New machine", "cost", 0, 150000),
                self._line("Salvage of old machine", "revenue", 0, 30000),
                self._line("Book value written off", "cost", 0, 80000, element_id=self.el_sunk.id),
            ],
        )
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 120000.0, "verdict": "alternative"}])

    def test_computed_decision_is_locked(self):
        # DIF-01 / AD-113: a saved verdict cannot drift under edited inputs
        decision = self._split_off()
        decision.action_compute()
        with self.assertRaises(UserError):
            decision.line_ids[:1].amount_alternative = 1.0
        with self.assertRaises(UserError):
            decision.write({"volume": 7.0})
        with self.assertRaises(UserError):
            decision.action_compute()

    def test_reset_clears_outputs(self):
        # DIF-01 / AD-113: reset returns to draft without a stale verdict
        decision = self._split_off()
        decision.action_compute()
        decision.action_reset_to_draft()
        self.assertRecordValues(decision, [{"state": "draft", "differential": 0.0, "verdict": False}])

    def test_outputs_written_by_compute_only(self):
        # DIF-01 / AD-001: the verdict is the engine's, not typed
        decision = self._split_off()
        with self.assertRaises(UserError):
            decision.with_user(self.user).write({"verdict": "alternative"})

    # ---- drop line (Cinquini III.4.1) -------------------------------------------------

    def _drop(self, avoidable_fixed):
        return self._decision(
            "drop_line",
            line_ids=[
                self._line("Revenue", "revenue", 100000, 999),  # typed alternative ignored: derived
                self._line("Variable cost", "cost", 70000, element_id=self.el_avoidable.id, cost_behavior="variable"),
                self._line("Specific fixed avoidable", "cost", avoidable_fixed, element_id=self.el_avoidable.id),
                self._line("Specific fixed committed", "cost", 8000, element_id=self.el_fixed.id),
                self._line("Common fixed allocated", "cost", 15000, element_id=self.el_fixed.id),
            ],
        )

    def test_drop_line_pays_when_avoidable_fixed_exceeds_margin(self):
        # DIF-01 / Cinquini III.4.1: -100,000 + 70,000 + 32,000 = +2,000 -> eliminate
        decision = self._drop(32000)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 2000.0, "verdict": "alternative"}])

    def test_drop_line_keeps_when_margin_exceeds_avoidable_fixed(self):
        # DIF-01 / Cinquini III.4.1: -100,000 + 70,000 + 20,000 = -10,000 -> keep
        decision = self._drop(20000)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": -10000.0, "verdict": "current"}])

    # ---- make or buy (DIF-03) ---------------------------------------------------------

    def _make_or_buy(self, volume, activities=None, buy_price_unit=12.0, **kw):
        unit = volume / 4000.0
        return self._decision(
            "make_or_buy",
            volume=volume,
            buy_price_unit=buy_price_unit,
            line_ids=[
                self._line("Direct material", "cost", 16000 * unit, element_id=self.el_avoidable.id, cost_behavior="variable"),
                self._line("Direct labour", "cost", 12000 * unit, element_id=self.el_avoidable.id, cost_behavior="variable"),
                self._line("Variable overhead", "cost", 4000 * unit, element_id=self.el_avoidable.id, cost_behavior="variable"),
                self._line("Avoidable fixed", "cost", 20000, element_id=self.el_avoidable.id),
                self._line("Common fixed", "cost", 30000, element_id=self.el_fixed.id),
            ],
            activity_ids=activities or [],
            **kw,
        )

    def test_make_or_buy_below_indifference_buys(self):
        # DIF-03 / Cinquini III.4.2: make 8x4,000 + 20,000 = 52,000 vs buy 48,000 -> buy; Q* = 5,000
        decision = self._make_or_buy(4000)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 4000.0, "verdict": "alternative", "verdict_label": "Buy",
                                            "has_indifference": True, "indifference_qty": 5000.0}])

    def test_make_or_buy_above_indifference_makes(self):
        # DIF-03: at 6,000 make 68,000 vs buy 72,000 -> make; common fixed cancels out
        decision = self._make_or_buy(6000)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": -4000.0, "verdict": "current", "verdict_label": "Make"}])

    def test_make_or_buy_opportunity_cost(self):
        # DIF-03: freed capacity worth 5,000 burdens make -> 6,000: 73,000 vs 72,000 -> buy
        decision = self._make_or_buy(6000, opportunity_cost=5000.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 1000.0, "verdict": "alternative"}])

    def test_make_or_buy_hidden_activity_costs(self):
        # DIF-03 / Turney Ch.8: 10 setups x 150 on make, 40 receipts x 25 on buy
        # Δ = 52,000 + 1,500 - (48,000 + 1,000) = 4,500; Q* = (20,000 + 1,500 - 1,000) / (12 - 8) = 5,125
        decision = self._make_or_buy(4000, activities=[
            self._act(self.c_setup, self.setups, 10, side="current"),
            self._act(self.c_receiving, self.receipts, 40, side="alternative"),
        ])
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 4500.0, "indifference_qty": 5125.0}])
        self.assertRecordValues(decision.activity_ids.sorted("id"), [
            {"rate_value": 150.0, "amount": 1500.0},
            {"rate_value": 25.0, "amount": 1000.0},
        ])

    def test_make_or_buy_without_indifference(self):
        # DIF-03: buying below the variable make cost is always cheaper -> no Q*
        decision = self._make_or_buy(4000, buy_price_unit=7.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"has_indifference": False, "indifference_qty": 0.0, "verdict": "alternative"}])

    def test_missing_rate_fails_loud(self):
        # DIF-03 / AD-108: an activity without a published rate is not a zero cost
        decision = self._make_or_buy(4000, activities=[self._act(self.c_setup, self.mh, 3, name="no rate here")])
        with self.assertRaisesRegex(UserError, "no rate here"):
            decision.action_compute()

    def test_rate_origin_cannot_be_reset_under_computed_decision(self):
        # DIF-03 / AD-002: the producer of the rates read by a computed decision stays computed
        decision = self._make_or_buy(4000, activities=[self._act(self.c_setup, self.setups, 10, side="current")])
        decision.action_compute()
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()
        decision.action_reset_to_draft()
        self.producer.action_reset_to_draft()
        self.assertEqual(self.producer.state, "draft")

    # ---- RN-001 verdict and Q* agree --------------------------------------------------

    def test_make_or_buy_opportunity_cost_moves_indifference(self):
        # RN-001: opportunity 5,000 burdens make -> Q* = (20,000 + 5,000) / (12 - 8) = 6,250; at 6,000 < Q* -> Buy
        decision = self._make_or_buy(6000, opportunity_cost=5000.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 1000.0, "verdict": "alternative", "verdict_label": "Buy",
                                            "has_indifference": True, "indifference_qty": 6250.0}])

    def test_make_or_buy_variable_cost_always_avoidable(self):
        # RN-001: material 40,000 variable WITHOUT element (avoidable False) still disappears when buying:
        # make 40,000 + 20,000 = 60,000 vs buy 12 x 4,000 = 48,000 -> Δ +12,000 Buy; Q* = 20,000 / (12 - 10) = 10,000 > 4,000
        decision = self._decision(
            "make_or_buy",
            volume=4000,
            buy_price_unit=12.0,
            line_ids=[
                self._line("Direct material", "cost", 40000, cost_behavior="variable"),
                self._line("Avoidable fixed", "cost", 20000, element_id=self.el_avoidable.id),
            ],
        )
        self.assertFalse(decision.line_ids.filtered(lambda line: line.name == "Direct material").avoidable)
        decision.action_compute()
        self.assertRecordValues(decision, [{"current_total": -60000.0, "alternative_total": -48000.0, "differential": 12000.0,
                                            "verdict": "alternative", "has_indifference": True, "indifference_qty": 10000.0}])

    def test_drop_line_variable_cost_always_avoidable(self):
        # RN-001: the same derivation for drop_line: an element-less variable cost goes with the line
        # -100,000 + 70,000 + 32,000 = +2,000 -> eliminate, as with the avoidable element
        decision = self._decision(
            "drop_line",
            line_ids=[
                self._line("Revenue", "revenue", 100000),
                self._line("Variable cost", "cost", 70000, cost_behavior="variable"),
                self._line("Specific fixed avoidable", "cost", 32000, element_id=self.el_avoidable.id),
                self._line("Common fixed allocated", "cost", 15000, element_id=self.el_fixed.id),
            ],
        )
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 2000.0, "verdict": "alternative", "verdict_label": "Eliminate line"}])

    # ---- scenarios of the removed transient cmc.differential.analysis (same numbers) ---

    def _unit_make_or_buy(self, make_unit, buy_unit, volume=1.0, **kw):
        return self._decision(
            "make_or_buy", volume=volume, buy_price_unit=buy_unit,
            line_ids=[self._line("Make cost", "cost", make_unit * volume, element_id=self.el_avoidable.id, cost_behavior="variable")],
            **kw,
        )

    def test_legacy_make_cheaper(self):
        # DIF-03 (ex test_make_or_buy_make): make 8 vs buy 10 -> Δ -2 -> Make
        decision = self._unit_make_or_buy(8.0, 10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": -2.0, "verdict": "current", "verdict_label": "Make"}])

    def test_legacy_buy_cheaper(self):
        # DIF-03 (ex test_make_or_buy_buy): make 12 vs buy 10 -> Δ +2 -> Buy
        decision = self._unit_make_or_buy(12.0, 10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 2.0, "verdict": "alternative", "verdict_label": "Buy"}])

    def test_legacy_volume_and_opportunity(self):
        # DIF-03 / RN-001 (ex test_make_or_buy_volume_and_opportunity): make 800 + 150 = 950 vs buy 1,000 -> Δ -50 Make;
        # Q* = 150 / (10 - 8) = 75 < 100 -> Make, consistent
        decision = self._unit_make_or_buy(8.0, 10.0, volume=100.0, opportunity_cost=150.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": -50.0, "verdict": "current", "has_indifference": True,
                                            "indifference_qty": 75.0}])

    def _legacy_drop(self, lost_contribution, avoidable_fixed):
        return self._decision("drop_line", line_ids=[
            self._line("Contribution", "revenue", lost_contribution),
            self._line("Avoidable fixed", "cost", avoidable_fixed, element_id=self.el_avoidable.id),
        ])

    def test_legacy_drop_line_keep(self):
        # DIF-01 (ex test_drop_line_keep): lost MC 500 > avoidable fixed 300 -> Δ -200 -> Keep line
        decision = self._legacy_drop(500.0, 300.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": -200.0, "verdict": "current", "verdict_label": "Keep line"}])

    def test_legacy_drop_line_eliminate(self):
        # DIF-01 (ex test_drop_line_eliminate): lost MC 200 < avoidable fixed 300 -> Δ +100 -> Eliminate line
        decision = self._legacy_drop(200.0, 300.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 100.0, "verdict": "alternative", "verdict_label": "Eliminate line"}])

    def test_legacy_replace(self):
        # DIF-01 (ex test_replace): keeping costs 1,000, replacing 800 -> Δ +200 -> Replace
        decision = self._decision("replace", line_ids=[self._line("Relevant cost", "cost", 1000.0, 800.0)])
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 200.0, "verdict": "alternative", "verdict_label": "Replace"}])

    def test_legacy_indifferent(self):
        # DIF-03 (ex test_indifferent): make 10 = buy 10 -> Δ 0 -> Indifferent
        decision = self._unit_make_or_buy(10.0, 10.0)
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 0.0, "verdict": "indifferent", "verdict_label": "Indifferent"}])

    def test_legacy_rounding_noise_is_indifferent(self):
        # DIF-03 (ex test_rounding_noise_is_indifferent): buy 10 x 1.0004 = 10.004 vs make 10 -> -0.004 below the currency step
        decision = self._decision("make_or_buy", volume=1.0004, buy_price_unit=10.0,
                                  line_ids=[self._line("Make cost", "cost", 10.0, element_id=self.el_avoidable.id)])
        decision.action_compute()
        self.assertRecordValues(decision, [{"differential": 0.0, "verdict": "indifferent"}])
