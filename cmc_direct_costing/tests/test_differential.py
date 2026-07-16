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
        self.assertEqual(w.verdict, "Make")

    def test_make_or_buy_buy(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=12, buy_unit_cost=10)
        self.assertEqual(w.differential, 2.0)
        self.assertEqual(w.verdict, "Buy")

    def test_make_or_buy_volume_and_opportunity(self):
        w = self._w(decision_type="make_or_buy", volume=100,
                    make_unit_cost=8, buy_unit_cost=10, opportunity_cost=150)
        # make = 800 + 150 = 950 ; buy = 1000 -> diff -50 -> Make
        self.assertEqual(w.differential, -50.0)
        self.assertEqual(w.verdict, "Make")

    def test_drop_line_keep(self):
        w = self._w(decision_type="drop_line",
                    lost_contribution=500, avoidable_fixed=300)
        self.assertEqual(w.verdict, "Keep line")

    def test_drop_line_eliminate(self):
        w = self._w(decision_type="drop_line",
                    lost_contribution=200, avoidable_fixed=300)
        self.assertEqual(w.verdict, "Eliminate line")

    def test_replace(self):
        w = self._w(decision_type="replace",
                    new_relevant_cost=1000, old_relevant_cost=800)
        self.assertEqual(w.differential, 200.0)
        self.assertEqual(w.verdict, "Replace")

    def test_indifferent(self):
        w = self._w(decision_type="make_or_buy", volume=1,
                    make_unit_cost=10, buy_unit_cost=10)
        self.assertEqual(w.differential, 0.0)
        self.assertEqual(w.verdict, "Indifferent")
