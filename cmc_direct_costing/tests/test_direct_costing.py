from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDirectCosting(TransactionCase):
    """TS-DC-001 — margine di contribuzione (lordo/semilordo)."""

    def _dc(self, lines, common=0.0):
        return self.env["cmc.direct.costing"].create(
            {"name": "dc", "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31),
             "common_fixed_cost": common,
             "line_ids": [(0, 0, ln) for ln in lines]}
        )

    # T1/T2 — MC lordo e semilordo per riga
    def test_margins(self):
        a = self._dc(
            [
                {"name": "A", "revenue": 1000, "variable_cost": 400, "specific_fixed_cost": 150},
                {"name": "B", "revenue": 800, "variable_cost": 300, "specific_fixed_cost": 100},
            ],
            common=200,
        )
        la = a.line_ids.filtered(lambda l: l.name == "A")
        self.assertEqual(la.mc_gross, 600.0)
        self.assertEqual(la.mc_semigross, 450.0)
        # T3 — risultato (DC evoluto): 1100 - 250 specifici? no: semigross 450+400=850; -200 comuni
        self.assertEqual(a.total_mc_gross, 1100.0)
        self.assertEqual(a.total_mc_semigross, 850.0)
        self.assertEqual(a.operating_result, 650.0)

    # T4 — incidenza MC %
    def test_mc_ratio(self):
        a = self._dc([{"name": "A", "revenue": 1000, "variable_cost": 400}])
        self.assertEqual(a.line_ids.mc_ratio, 60.0)

    # T5 — ricavi nulli: nessuna divisione per zero
    def test_zero_revenue(self):
        a = self._dc([{"name": "A", "revenue": 0, "variable_cost": 0}])
        self.assertEqual(a.line_ids.mc_ratio, 0.0)
