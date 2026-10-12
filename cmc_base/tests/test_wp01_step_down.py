"""WP-01 / ALC-04 - step-down closing in a controlled order (service share or user-defined sequence).

CONTRACT
- ``cmc.closing.run`` new input field ``step_order`` Selection ``auto`` (default: current behaviour, by number
  of to-close centers served, then id) / ``service_share`` / ``manual``; new input O2m ``step_ids`` ->
  ``cmc.closing.step`` (``run_id``, ``sequence`` Integer, ``center_id`` M2o account.analytic.account;
  ``cmc.run.line.mixin``: locked while the run is computed). Both are in ``_cmc_input_fields``. Only the
  ``step_down`` method uses them.
- ``service_share``: providers ordered by the share of their service units rendered to OTHER to-close centers
  (units to to-close receivers / all units), highest first; ties by id.
- ``manual``: providers closed in ``step_ids`` order (sequence, then id). Every to-close center with cost must
  be listed, else ValidationError at compute (fail loud). A closed center receives nothing more.

Hand-computed (2031): A cost 20000, serves B 300 and F1 500; B cost 10000, serves A 400 and F2 600.
  A first (auto: tie on served count, A has the lower id): B gets 20000*300/800 = 7500, F1 12500;
    B closes 17500 to F2 (A already closed) -> F1 12500, F2 17500.
  B first: A gets 10000*400/1000 = 4000, F2 6000; A closes 24000 to F1 (B closed) -> F1 24000, F2 6000.
  service_share: A 300/800 = 0.375, B 400/1000 = 0.4 -> B first.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

SPAN = {"date_from": date(2031, 1, 1), "date_to": date(2031, 12, 31)}


@tagged("post_install", "-at_install")
class TestStepDownOrder(TransactionCase):
    """ALC-04: the closing order is a decision of the controller, not an accident of ids."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Run = cls.env["cmc.closing.run"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "WP01 SD hours", "code": "WP01SD_H"})
        cls.aux_a = cls._center("WP01 SD A", "auxiliary")
        cls.aux_b = cls._center("WP01 SD B", "auxiliary")
        cls.f1 = cls._center("WP01 SD F1", "final")
        cls.f2 = cls._center("WP01 SD F2", "final")
        for center, amount in ((cls.aux_a, 20000.0), (cls.aux_b, 10000.0)):
            cls.AL.create({"name": "cost", "account_id": center.id, "amount": -amount, "date": date(2031, 6, 15)})
        for provider, receiver, qty in ((cls.aux_a, cls.aux_b, 300), (cls.aux_a, cls.f1, 500),
                                        (cls.aux_b, cls.aux_a, 400), (cls.aux_b, cls.f2, 600)):
            cls.env["cmc.center.service"].create({"provider_id": provider.id, "receiver_id": receiver.id,
                                                  "driver_id": cls.driver.id, "quantity": qty})

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype})

    def _received(self, run):
        return {center: run.line_ids.filtered(lambda line: line.center_id == center).cost_received
                for center in (self.f1, self.f2)}

    def test_auto_order_unchanged(self):
        # ALC-04: default keeps the current order (A first on the tie)
        run = self.Run.create({**SPAN, "method": "step_down"})
        self.assertEqual(run.step_order, "auto")
        run.action_compute()
        received = self._received(run)
        self.assertAlmostEqual(received[self.f1], 12500.0)
        self.assertAlmostEqual(received[self.f2], 17500.0)

    def test_manual_sequence(self):
        # ALC-04: user-defined sequence B then A; a closed center receives nothing more
        run = self.Run.create({**SPAN, "method": "step_down", "step_order": "manual", "step_ids": [
            (0, 0, {"sequence": 1, "center_id": self.aux_b.id}),
            (0, 0, {"sequence": 2, "center_id": self.aux_a.id}),
        ]})
        run.action_compute()
        received = self._received(run)
        self.assertAlmostEqual(received[self.f1], 24000.0)
        self.assertAlmostEqual(received[self.f2], 6000.0)
        line_b = run.line_ids.filtered(lambda line: line.center_id == self.aux_b)
        self.assertAlmostEqual(line_b.cost_received, 0.0)
        self.assertAlmostEqual(line_b.cost_residual, 0.0)

    def test_manual_sequence_a_first(self):
        # ALC-04: e.g. A->B: 20,000 x (1 - 300/800) to finals
        run = self.Run.create({**SPAN, "method": "step_down", "step_order": "manual", "step_ids": [
            (0, 0, {"sequence": 1, "center_id": self.aux_a.id}),
            (0, 0, {"sequence": 2, "center_id": self.aux_b.id}),
        ]})
        run.action_compute()
        self.assertAlmostEqual(self._received(run)[self.f1], 12500.0)

    def test_manual_sequence_incomplete_fails_loud(self):
        # ALC-04 / INT-05: a to-close center left out of the sequence would keep its cost
        run = self.Run.create({**SPAN, "method": "step_down", "step_order": "manual", "step_ids": [
            (0, 0, {"sequence": 1, "center_id": self.aux_b.id}),
        ]})
        with self.assertRaises(ValidationError):
            run.action_compute()

    def test_service_share_order(self):
        # ALC-04: the provider with the larger share of service to other intermediates closes first
        run = self.Run.create({**SPAN, "method": "step_down", "step_order": "service_share"})
        run.action_compute()
        received = self._received(run)
        self.assertAlmostEqual(received[self.f1], 24000.0)
        self.assertAlmostEqual(received[self.f2], 6000.0)

    def test_sequence_locked_when_computed(self):
        # AD-001: the sequence is an input of the computed run
        run = self.Run.create({**SPAN, "method": "step_down", "step_order": "manual", "step_ids": [
            (0, 0, {"sequence": 1, "center_id": self.aux_b.id}),
            (0, 0, {"sequence": 2, "center_id": self.aux_a.id}),
        ]})
        run.action_compute()
        with self.assertRaises(UserError):
            run.step_ids[0].sequence = 5
        with self.assertRaises(UserError):
            run.step_order = "auto"
