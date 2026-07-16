from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCapacity(TransactionCase):
    """TS-007 — Capacità produttiva e costo della capacità inutilizzata."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.CA = cls.env["cmc.capacity.analysis"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create(
            {"name": "Centro Z", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.d_from = date(2026, 1, 1)
        cls.d_to = date(2026, 1, 31)

    def _fixed(self, amount):
        self.AL.create(
            {
                "name": "fixed cost",
                "account_id": self.center.id,
                "amount": amount,
                "date": date(2026, 1, 15),
                "cmc_behavior": "fixed",
            }
        )

    def _variable(self, amount):
        self.AL.create(
            {
                "name": "var cost",
                "account_id": self.center.id,
                "amount": amount,
                "date": date(2026, 1, 15),
                "cmc_behavior": "variable",
            }
        )

    def _analysis(self, practical, actual, theoretical=0.0):
        return self.CA.create(
            {
                "center_id": self.center.id,
                "date_from": self.d_from,
                "date_to": self.d_to,
                "capacity_theoretical": theoretical,
                "capacity_practical": practical,
                "capacity_actual": actual,
            }
        )

    # T1 — tre livelli di capacità memorizzati
    def test_t1_three_levels(self):
        ca = self._analysis(practical=120, actual=80, theoretical=150)
        self.assertEqual(ca.capacity_theoretical, 150)
        self.assertEqual(ca.capacity_practical, 120)
        self.assertEqual(ca.capacity_actual, 80)

    # T2 — coefficiente sui costi fissi su base capacità PRATICA (2400/120 = 20)
    def test_t2_coefficient_on_practical(self):
        self._fixed(2400)
        self._variable(999)  # i variabili NON entrano nel pool fisso
        ca = self._analysis(practical=120, actual=120)
        self.assertEqual(ca.fixed_cost, 2400.0)
        self.assertEqual(ca.fixed_cost_coefficient, 20.0)

    # T3 — periodo pieno: nessuna capacità inutilizzata
    def test_t3_full_utilization(self):
        self._fixed(2400)
        ca = self._analysis(practical=120, actual=120)
        self.assertEqual(ca.imputed_fixed_cost, 2400.0)
        self.assertEqual(ca.unused_capacity_cost, 0.0)

    # T4 — basso utilizzo: idle isolato (80/120 → imputato 1600, inutilizzato 800)
    def test_t4_low_utilization_isolates_idle(self):
        self._fixed(2400)
        ca = self._analysis(practical=120, actual=80)
        self.assertEqual(ca.imputed_fixed_cost, 1600.0)
        self.assertEqual(ca.unused_capacity_cost, 800.0)

    # T5 — coefficiente (costo unitario fisso) STABILE al variare dell'utilizzo
    # (la base è la capacità pratica, non l'output effettivo)
    def test_t5_unit_cost_stable(self):
        self._fixed(2400)
        ca = self._analysis(practical=120, actual=120)
        coeff_full = ca.fixed_cost_coefficient
        ca.capacity_actual = 60  # crolla l'utilizzo
        self.assertEqual(ca.fixed_cost_coefficient, coeff_full)
        self.assertEqual(ca.fixed_cost_coefficient, 20.0)

    # Conservazione: imputato + inutilizzato = costo fisso totale
    def test_conservation(self):
        self._fixed(2401)  # importo che non divide esatto su 120
        ca = self._analysis(practical=120, actual=80)
        self.assertEqual(
            ca.imputed_fixed_cost + ca.unused_capacity_cost, 2401.0
        )

    # T6 — il report espone l'idle per centro/periodo
    def test_t6_idle_reported(self):
        self._fixed(2400)
        ca = self._analysis(practical=120, actual=80)
        self.assertEqual(ca.utilization_rate, 80.0 / 120.0 * 100)
        self.assertEqual(ca.unused_capacity_cost, 800.0)

    # Staleness — i costi derivati si aggiornano se cambiano le voci di costo
    # DOPO la creazione dell'analisi (integrità, non bottone manuale)
    def test_recompute_on_line_change(self):
        ca = self._analysis(practical=120, actual=80)  # nessun costo fisso ancora
        self.assertEqual(ca.fixed_cost, 0.0)
        self._fixed(2400)  # aggiungo il costo fisso DOPO
        self.assertEqual(ca.fixed_cost, 2400.0)
        self.assertEqual(ca.unused_capacity_cost, 800.0)

    # Guard — capacità negativa bloccata
    def test_negative_capacity_blocked(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._analysis(practical=-10, actual=5)
                self.env.flush_all()

    # Guard — actual > practical: warning (non blocco)
    def test_overcapacity_warns(self):
        ca = self._analysis(practical=120, actual=120)
        ca.capacity_actual = 130
        result = ca._warn_overcapacity()
        self.assertIn("warning", result)
