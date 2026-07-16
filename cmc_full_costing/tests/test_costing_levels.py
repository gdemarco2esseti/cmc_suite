from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCostingLevels(TransactionCase):
    """TS-002 (figurativi) + TS-006 (configurazioni di costo a 4 livelli)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        cls.z = cls._center("Z", "final")
        cls.w = cls._center("W", "final")
        cls._cost(cls.z, 1000)
        cls._cost(cls.w, 600)
        # period (commercial/admin) center: NO center_type -> out of closing pool
        cls.comm = cls.AA.create(
            {"name": "Commerciale", "plan_id": cls.plan.id, "cmc_is_period": True}
        )
        cls._cost(cls.comm, 200)
        cls.closing = cls.env["cmc.closing.run"].create(
            {"name": "Chiusura", "method": "direct"}
        )
        cls.closing.action_compute()
        cls.p1 = cls.env["product.product"].create({"name": "P1", "standard_price": 50})
        cls.p2 = cls.env["product.product"].create({"name": "P2", "standard_price": 80})
        # figurative cost: 100 direct to P1
        cls.fig_el = cls.env["cmc.cost.element"].create(
            {"name": "Interessi di computo", "code": "INT", "cmc_kind": "figurative"}
        )
        cls.fig = cls.env["cmc.figurative.cost"].create(
            {
                "name": "Interessi P1",
                "cost_element_id": cls.fig_el.id,
                "method": "lump",
                "amount": 100,
                "cost_object_ref": f"product.product,{cls.p1.id}",
                "cmc_directness": "direct",
            }
        )

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype}
        )

    @classmethod
    def _cost(cls, center, amount):
        cls.AL.create(
            {"name": "c", "account_id": center.id, "amount": amount,
             "cmc_behavior": "fixed"}
        )

    def _run(self):
        lines = [
            {"center_id": self.z.id, "product_id": self.p1.id, "driver_id": self.driver.id, "driver_qty": 60},
            {"center_id": self.z.id, "product_id": self.p2.id, "driver_id": self.driver.id, "driver_qty": 40},
            {"center_id": self.w.id, "product_id": self.p1.id, "driver_id": self.driver.id, "driver_qty": 30},
            {"center_id": self.w.id, "product_id": self.p2.id, "driver_id": self.driver.id, "driver_qty": 30},
        ]
        run = self.env["cmc.costing.run"].create(
            {
                "name": "Costing",
                "closing_run_id": self.closing.id,
                "base": "multiple",
                "product_ids": [(6, 0, (self.p1 | self.p2).ids)],
                "line_ids": [(0, 0, ln) for ln in lines],
            }
        )
        run.action_compute()
        return run

    def _r(self, run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)

    # TS-006 — il costo industriale NON include i costi di periodo (commerciali)
    def test_period_excluded_from_industrial(self):
        r1 = self._r(self._run(), self.p1)
        self.assertEqual(r1.cost_industriale, 950.0)  # 50 primo + 900 indiretto

    # TS-006 — complessivo = industriale + quota costi commerciali/amministrativi
    def test_complessivo(self):
        r1 = self._r(self._run(), self.p1)
        # pool 200, base unità: P1 90 / P2 70 (tot 160) -> P1 = 200*90/160 = 112.5
        self.assertEqual(r1.cost_comm_admin, 112.5)
        self.assertEqual(r1.cost_complessivo, 1062.5)

    # TS-006 + TS-002 — economico-tecnico = complessivo + figurativi
    def test_economico_tecnico(self):
        r1 = self._r(self._run(), self.p1)
        self.assertEqual(r1.cost_figurativo, 100.0)
        self.assertEqual(r1.cost_economico_tecnico, 1162.5)

    # TS-006 — progressione monotona dei 4 livelli
    def test_levels_monotonic(self):
        r1 = self._r(self._run(), self.p1)
        self.assertLessEqual(r1.cost_primo, r1.cost_industriale)
        self.assertLessEqual(r1.cost_industriale, r1.cost_complessivo)
        self.assertLessEqual(r1.cost_complessivo, r1.cost_economico_tecnico)

    # TS-006 — conservazione dei costi di periodo sui prodotti
    def test_period_conserved(self):
        run = self._run()
        self.assertEqual(sum(run.result_ids.mapped("cost_comm_admin")), 200.0)

    # TS-006 — riconciliazione vs standard_price (variance)
    def test_reconciliation_variance(self):
        r1 = self._r(self._run(), self.p1)
        self.assertEqual(r1.standard_price_ref, 50.0)
        self.assertEqual(r1.std_price_delta, 900.0)  # 950 - 50

    # TS-006 — tracciabilità dei 4 livelli
    def test_config_traceability(self):
        r1 = self._r(self._run(), self.p1)
        levels = set(r1.config_ids.mapped("level"))
        self.assertEqual(
            levels, {"primo", "industriale", "complessivo", "economico_tecnico"}
        )

    # TS-002 — un costo figurativo deve usare un elemento figurativo
    def test_figurative_requires_figurative_element(self):
        real_el = self.env["cmc.cost.element"].create(
            {"name": "Materie", "code": "MAT2", "cmc_kind": "real"}
        )
        with self.assertRaises(ValidationError):
            self.env["cmc.figurative.cost"].create(
                {"name": "x", "cost_element_id": real_el.id,
                 "method": "lump", "amount": 50}
            )

    # TS-002 — interessi = rate * capitale
    def test_figurative_interest(self):
        fc = self.env["cmc.figurative.cost"].create(
            {"name": "Int", "cost_element_id": self.fig_el.id,
             "method": "interest", "capital": 10000, "rate": 0.05}
        )
        self.assertEqual(fc.amount, 500.0)

    # Gate fix1 — un figurativo fuori dalla finestra del run NON deve entrare
    def test_figurative_out_of_period_excluded(self):
        from datetime import date
        self.env["cmc.figurative.cost"].create(
            {"name": "Vecchio", "cost_element_id": self.fig_el.id, "method": "lump",
             "amount": 999, "cmc_directness": "indirect",
             "date_from": date(2020, 1, 1), "date_to": date(2020, 12, 31)}
        )
        run = self.env["cmc.costing.run"].create(
            {
                "name": "Costing 2026",
                "closing_run_id": self.closing.id,
                "base": "multiple",
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 31),
                "product_ids": [(6, 0, (self.p1 | self.p2).ids)],
                "line_ids": [
                    (0, 0, {"center_id": self.z.id, "product_id": self.p1.id,
                            "driver_id": self.driver.id, "driver_qty": 60}),
                    (0, 0, {"center_id": self.z.id, "product_id": self.p2.id,
                            "driver_id": self.driver.id, "driver_qty": 40}),
                    (0, 0, {"center_id": self.w.id, "product_id": self.p1.id,
                            "driver_id": self.driver.id, "driver_qty": 30}),
                    (0, 0, {"center_id": self.w.id, "product_id": self.p2.id,
                            "driver_id": self.driver.id, "driver_qty": 30}),
                ],
            }
        )
        run.action_compute()
        # only the undated direct fig (100 on P1) counts, not the 2020 one
        self.assertEqual(sum(run.result_ids.mapped("cost_figurativo")), 100.0)

    # Gate fix2 — un centro di periodo NON può ricevere un servizio
    def test_period_center_cannot_receive(self):
        with self.assertRaises(ValidationError):
            self.env["cmc.center.service"].create(
                {"provider_id": self.z.id, "receiver_id": self.comm.id,
                 "driver_id": self.driver.id, "quantity": 10}
            )
