from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCostCenters(TransactionCase):
    """TS-003 — Piano dei centri di costo e localizzazione dei costi."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.driver = cls.env["cmc.cost.driver"].create(
            {"name": "Ore servizio", "code": "HRS"}
        )
        cls.aux = cls._center("Manutenzione", "auxiliary")
        cls.final_a = cls._center("Montaggio Z", "final")
        cls.final_b = cls._center("Montaggio Y", "final")
        cls.final_c = cls._center("Montaggio X", "final")

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype}
        )

    # T1 — tipizzazione del centro
    def test_t1_center_type(self):
        self.assertEqual(self.aux.cmc_center_type, "auxiliary")
        self.assertEqual(self.final_a.cmc_center_type, "final")

    # Gerarchia — parent/child e prevenzione cicli
    def test_hierarchy_and_cycle(self):
        child = self._center("Sub Montaggio", "final")
        child.cmc_parent_id = self.final_a
        self.assertIn(child, self.final_a.cmc_child_ids)
        with self.assertRaises(ValidationError):
            self.final_a.cmc_parent_id = child  # ciclo

    # T2 — localizzazione DIRETTA: intero importo su un solo centro
    def test_t2_direct_localization(self):
        alloc = self.env["cmc.allocation"].create(
            {
                "name": "Diretta",
                "source_account_id": self.aux.id,
                "driver_id": self.driver.id,
                "amount": 1000.0,
                "line_ids": [(0, 0, {"target_account_id": self.final_a.id, "quantity": 1})],
            }
        )
        self.assertEqual(alloc.line_ids.allocated_amount, 1000.0)

    # T3 — localizzazione per base di riparto: la somma conserva il totale
    def test_t3_allocation_conserves_total(self):
        alloc = self.env["cmc.allocation"].create(
            {
                "name": "Riparto 3 centri",
                "source_account_id": self.aux.id,
                "driver_id": self.driver.id,
                "amount": 1000.0,
                "line_ids": [
                    (0, 0, {"target_account_id": self.final_a.id, "quantity": 1}),
                    (0, 0, {"target_account_id": self.final_b.id, "quantity": 1}),
                    (0, 0, {"target_account_id": self.final_c.id, "quantity": 1}),
                ],
            }
        )
        self.assertEqual(sum(alloc.line_ids.mapped("allocated_amount")), 1000.0)

    # T3-bis — arrotondamento: 100 su 3 centri ricompone 100.00 esatto
    def test_t3_rounding_conserves(self):
        alloc = self.env["cmc.allocation"].create(
            {
                "name": "Riparto arrotondamento",
                "source_account_id": self.aux.id,
                "driver_id": self.driver.id,
                "amount": 100.0,
                "line_ids": [
                    (0, 0, {"target_account_id": self.final_a.id, "quantity": 1}),
                    (0, 0, {"target_account_id": self.final_b.id, "quantity": 1}),
                    (0, 0, {"target_account_id": self.final_c.id, "quantity": 1}),
                ],
            }
        )
        amounts = sorted(alloc.line_ids.mapped("allocated_amount"))
        self.assertEqual(sum(amounts), 100.0)
        self.assertEqual(amounts, [33.33, 33.33, 33.34])

    # T3-ter — riparto proporzionale ai pesi (60/60/20 → 600/600/200 su 1400... usa 1.400)
    def test_t3_weighted(self):
        alloc = self.env["cmc.allocation"].create(
            {
                "name": "Riparto pesato",
                "source_account_id": self.aux.id,
                "driver_id": self.driver.id,
                "amount": 1400.0,
                "line_ids": [
                    (0, 0, {"target_account_id": self.final_a.id, "quantity": 60}),
                    (0, 0, {"target_account_id": self.final_b.id, "quantity": 60}),
                    (0, 0, {"target_account_id": self.final_c.id, "quantity": 20}),
                ],
            }
        )
        by_center = {l.target_account_id: l.allocated_amount for l in alloc.line_ids}
        self.assertEqual(by_center[self.final_a], 600.0)
        self.assertEqual(by_center[self.final_c], 200.0)
        self.assertEqual(sum(alloc.line_ids.mapped("allocated_amount")), 1400.0)

    # T5 — base di riparto a somma nulla deve essere bloccata
    def test_t5_zero_base_blocked(self):
        with self.assertRaises(ValidationError):
            self.env["cmc.allocation"].create(
                {
                    "name": "Base nulla",
                    "source_account_id": self.aux.id,
                    "driver_id": self.driver.id,
                    "amount": 500.0,
                    "line_ids": [
                        (0, 0, {"target_account_id": self.final_a.id, "quantity": 0}),
                        (0, 0, {"target_account_id": self.final_b.id, "quantity": 0}),
                    ],
                }
            )

    # T4 — relazione di servizio tra centri (ausiliario → ricevente)
    def test_t4_center_service(self):
        svc = self.env["cmc.center.service"].create(
            {
                "provider_id": self.aux.id,
                "receiver_id": self.final_a.id,
                "driver_id": self.driver.id,
                "quantity": 60,
            }
        )
        self.assertEqual(svc.provider_id, self.aux)
        self.assertEqual(svc.receiver_id, self.final_a)

    # T4-bis — un centro non può servire se stesso
    def test_t4_no_self_service(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env["cmc.center.service"].create(
                    {
                        "provider_id": self.aux.id,
                        "receiver_id": self.aux.id,
                        "driver_id": self.driver.id,
                        "quantity": 10,
                    }
                )
                self.env.flush_all()
