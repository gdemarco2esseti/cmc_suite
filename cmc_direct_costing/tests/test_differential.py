from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDifferential(TransactionCase):
    """TS-DIFF — make-or-buy, eliminazione linea, sostituzione (costi rilevanti)."""

    def _w(self, **kw):
        return self.env["cmc.differential.analysis"].create(kw)

    def test_make_or_buy_make(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=8, buy_unit_cost=10)
        self.assertEqual(w.differential, -2.0)
        self.assertLess(w.differential, 0.0)  # cheaper to make

    def test_make_or_buy_buy(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=12, buy_unit_cost=10)
        self.assertEqual(w.differential, 2.0)
        self.assertGreater(w.differential, 0.0)  # cheaper to buy

    def test_make_or_buy_volume_and_opportunity(self):
        w = self._w(decision_type="make_or_buy", volume=100,
                    make_unit_cost=8, buy_unit_cost=10, opportunity_cost=150)
        # make = 800 + 150 = 950 ; buy = 1000 -> diff -50 -> Make
        self.assertEqual(w.differential, -50.0)
        self.assertLess(w.differential, 0.0)  # cheaper to make

    def test_drop_line_keep(self):
        w = self._w(decision_type="drop_line",
                    lost_contribution=500, avoidable_fixed=300)
        self.assertGreater(w.differential, 0.0)  # keeping the line pays

    def test_drop_line_eliminate(self):
        w = self._w(decision_type="drop_line",
                    lost_contribution=200, avoidable_fixed=300)
        self.assertLess(w.differential, 0.0)  # dropping the line pays

    def test_replace(self):
        w = self._w(decision_type="replace",
                    keep_relevant_cost=1000, replace_relevant_cost=800)
        self.assertEqual(w.differential, 200.0)
        self.assertGreater(w.differential, 0.0)  # keeping costs more -> replace

    def test_indifferent(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=10, buy_unit_cost=10)
        self.assertEqual(w.differential, 0.0)

    # rounding noise must not read as a confident verdict (currency-aware)
    def test_rounding_noise_is_indifferent(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=10.004, buy_unit_cost=10.0)
        self.assertEqual(w.verdict, "Indifferent")

    def test_verdict_literals_are_extractable(self):
        # the mapping is built from literals inside the method, so each string
        # reaches the .pot; check the three pairs are wired to the right sign
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=12, buy_unit_cost=10)
        self.assertEqual(w.verdict, "Buy")
        w.decision_type = "drop_line"
        w.lost_contribution, w.avoidable_fixed = 500, 300
        self.assertEqual(w.verdict, "Keep line")
        w.decision_type = "replace"
        w.keep_relevant_cost, w.replace_relevant_cost = 1000, 800
        self.assertEqual(w.verdict, "Replace")
