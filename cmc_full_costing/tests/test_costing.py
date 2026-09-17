from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCosting(TransactionCase):
    """TS-005 — Full costing a base unica e a base multipla."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        # two final centers carrying indirect cost
        cls.z = cls._center("Centro Z", "final")
        cls.w = cls._center("Centro W", "final")
        cls._cost(cls.z, 1000)
        cls._cost(cls.w, 600)
        # closing run -> final centers absorb their own cost (no auxiliaries)
        cls.closing = cls.env["cmc.closing.run"].create(
            {"name": "Chiusura", "method": "direct"}
        )
        cls.closing.action_compute()
        # products with a direct (primo) cost
        cls.p1 = cls.env["product.product"].create(
            {"name": "P1", "standard_price": 50}
        )
        cls.p2 = cls.env["product.product"].create(
            {"name": "P2", "standard_price": 80}
        )

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype}
        )

    @classmethod
    def _cost(cls, center, amount):
        cls.AL.create(
            {"name": "ind", "account_id": center.id, "amount": amount,
             "cmc_behavior": "fixed", "cmc_directness": "indirect"}
        )

    def _run(self, base, lines, single_driver=False):
        vals = {
            "name": "Costing",
            "closing_run_id": self.closing.id,
            "date_from": date(date.today().year, 1, 1),
            "date_to": date(date.today().year, 12, 31),
            "base": base,
            "product_ids": [(6, 0, (self.p1 | self.p2).ids)],
            "line_ids": [(0, 0, ln) for ln in lines],
        }
        if single_driver:
            vals["single_driver_id"] = self.driver.id
        run = self.env["cmc.costing.run"].create(vals)
        run.action_compute()
        return run

    def _lines(self):
        # Z: P1 60h, P2 40h (base 100 -> coeff 10) ; W: P1 30, P2 30 (base 60 -> coeff 10)
        return [
            {"center_id": self.z.id, "product_id": self.p1.id, "driver_id": self.driver.id, "driver_qty": 60},
            {"center_id": self.z.id, "product_id": self.p2.id, "driver_id": self.driver.id, "driver_qty": 40},
            {"center_id": self.w.id, "product_id": self.p1.id, "driver_id": self.driver.id, "driver_qty": 30},
            {"center_id": self.w.id, "product_id": self.p2.id, "driver_id": self.driver.id, "driver_qty": 30},
        ]

    def _result(self, run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)

    # T2 — base MULTIPLA: un coefficiente per centro finale
    def test_multiple_base(self):
        run = self._run("multiple", self._lines())
        r1 = self._result(run, self.p1)
        # Z: 10*60=600 ; W: 10*30=300 -> 900 indiretto ; primo 50
        self.assertEqual(r1.cost_primo, 50.0)
        self.assertEqual(r1.cost_indiretto, 900.0)
        self.assertEqual(r1.cost_industriale, 950.0)

    # T5 — conservazione: la somma degli indiretti = totale pool finali (1600)
    def test_conservation_multiple(self):
        run = self._run("multiple", self._lines())
        tot = sum(run.result_ids.mapped("cost_indiretto"))
        self.assertEqual(tot, 1600.0)

    # Tracciabilità — quota per centro che compone l'indiretto del prodotto
    def test_traceability_quotas(self):
        run = self._run("multiple", self._lines())
        r1 = self._result(run, self.p1)
        self.assertEqual(len(r1.quota_ids), 2)  # Z e W
        self.assertEqual(sum(r1.quota_ids.mapped("amount")), r1.cost_indiretto)
        by_center = {q.center_id: q.amount for q in r1.quota_ids}
        self.assertEqual(by_center[self.z], 600.0)
        self.assertEqual(by_center[self.w], 300.0)

    # T1 — base UNICA: un solo coefficiente di stabilimento
    def test_single_base(self):
        run = self._run("single", self._lines(), single_driver=True)
        # pool 1600, base totale 160 (90 P1 + 70 P2) -> coeff 10
        r1 = self._result(run, self.p1)
        r2 = self._result(run, self.p2)
        self.assertEqual(r1.cost_indiretto, 900.0)  # 10*90
        self.assertEqual(r2.cost_indiretto, 700.0)  # 10*70
        self.assertEqual(
            sum(run.result_ids.mapped("cost_indiretto")), 1600.0
        )

    # base unica senza driver di stabilimento -> errore
    def test_single_base_requires_driver(self):
        with self.assertRaises(ValidationError):
            self._run("single", self._lines(), single_driver=False)

    # C1 — un prodotto consumato dalle righe ma fuori da product_ids deve
    # comunque essere costato (conservazione: nessuna quota persa)
    def test_conservation_orphan_product(self):
        run = self.env["cmc.costing.run"].create(
            {
                "name": "Costing",
                "closing_run_id": self.closing.id,
                "date_from": date(date.today().year, 1, 1),
                "date_to": date(date.today().year, 12, 31),
                "base": "multiple",
                "product_ids": [(6, 0, self.p1.ids)],  # solo P1 nello scope
                "line_ids": [(0, 0, ln) for ln in self._lines()],  # righe per P1 e P2
            }
        )
        run.action_compute()
        self.assertEqual(sum(run.result_ids.mapped("cost_indiretto")), 1600.0)
        self.assertIn(self.p2, run.result_ids.mapped("product_id"))

    # centro finale con costo ma senza base di imputazione -> errore
    def test_no_base_raises(self):
        lines = [
            {"center_id": self.z.id, "product_id": self.p1.id,
             "driver_id": self.driver.id, "driver_qty": 100},
        ]  # W ha 600 di costo ma nessuna riga -> nessuna base
        with self.assertRaises(ValidationError):
            self._run("multiple", lines)
