from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestClosing(TransactionCase):
    """TS-004 — Ribaltamento e chiusura dei centri intermedi/ausiliari."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Run = cls.env["cmc.closing.run"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        # closing runs always carry a period: the whole current year covers
        # the analytic lines below (explicitly dated mid-year)
        cls.period = {"date_from": date(date.today().year, 1, 1), "date_to": date(date.today().year, 12, 31)}

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype}
        )

    def _cost(self, center, amount):
        # cost pool localized on a center (positive magnitude for the test)
        self.AL.create(
            {"name": "cost", "account_id": center.id, "amount": amount, "date": date(date.today().year, 6, 15)}
        )

    def _service(self, provider, receiver, qty):
        return self.env["cmc.center.service"].create(
            {
                "provider_id": provider.id,
                "receiver_id": receiver.id,
                "driver_id": self.driver.id,
                "quantity": qty,
            }
        )

    def _line(self, run, center):
        return run.line_ids.filtered(lambda l: l.center_id == center)

    # T1 — chiusura semplice (esempio: 3600/120h → 1800 + 1800)
    def test_t1_direct_simple(self):
        aux = self._center("Manutenzione", "auxiliary")
        z = self._center("Montaggio Z", "final")
        y = self._center("Montaggio Y", "final")
        self._cost(aux, 3600)
        self._service(aux, z, 60)
        self._service(aux, y, 60)
        run = self.Run.create({**self.period, "method": "direct"})
        run.action_compute()
        self.assertEqual(self._line(run, z).cost_received, 1800.0)
        self.assertEqual(self._line(run, y).cost_received, 1800.0)
        self.assertEqual(self._line(run, aux).cost_residual, 0.0)

    # T5 — conservazione del totale
    def test_t5_total_conserved(self):
        aux = self._center("Aux", "auxiliary")
        z = self._center("Z", "final")
        y = self._center("Y", "final")
        self._cost(aux, 3600)
        self._service(aux, z, 60)
        self._service(aux, y, 60)
        run = self.Run.create({**self.period, "method": "direct"})
        run.action_compute()
        finals_received = sum(
            run.line_ids.filtered(
                lambda l: l.center_id.cmc_center_type == "final"
            ).mapped("cost_received")
        )
        self.assertEqual(finals_received, 3600.0)

    # T2 — chiusura a cascata (step-down)
    def test_t2_step_down(self):
        aux1 = self._center("Manutenzione", "auxiliary")
        aux2 = self._center("Energia", "auxiliary")
        z = self._center("Z", "final")
        y = self._center("Y", "final")
        self._cost(aux1, 3600)
        self._cost(aux2, 1000)
        # aux1 serves aux2 (downstream) and final Z
        self._service(aux1, aux2, 30)
        self._service(aux1, z, 90)
        # aux2 serves the two finals
        self._service(aux2, z, 1)
        self._service(aux2, y, 1)
        run = self.Run.create({**self.period, "method": "step_down"})
        run.action_compute()
        # both auxiliaries fully closed
        self.assertEqual(self._line(run, aux1).cost_residual, 0.0)
        self.assertEqual(self._line(run, aux2).cost_residual, 0.0)
        # conservation: finals absorb the whole 4600
        finals_received = sum(
            run.line_ids.filtered(
                lambda l: l.center_id.cmc_center_type == "final"
            ).mapped("cost_received")
        )
        self.assertEqual(finals_received, 4600.0)

    # T3 — prestazioni reciproche (metodo reciproco): quadra algebricamente
    def test_t3_reciprocal(self):
        aux1 = self._center("Manutenzione", "auxiliary")
        aux2 = self._center("Energia", "auxiliary")
        z = self._center("Z", "final")
        y = self._center("Y", "final")
        self._cost(aux1, 3600)
        self._cost(aux2, 1000)
        # reciprocal services between the two auxiliaries
        self._service(aux1, aux2, 20)
        self._service(aux1, z, 40)
        self._service(aux1, y, 40)
        self._service(aux2, aux1, 10)
        self._service(aux2, z, 45)
        self._service(aux2, y, 45)
        run = self.Run.create({**self.period, "method": "reciprocal"})
        run.action_compute()
        self.assertEqual(self._line(run, aux1).cost_residual, 0.0)
        self.assertEqual(self._line(run, aux2).cost_residual, 0.0)
        finals_received = sum(
            run.line_ids.filtered(
                lambda l: l.center_id.cmc_center_type == "final"
            ).mapped("cost_received")
        )
        self.assertEqual(finals_received, 4600.0)

    # T4 — ripetibilità del run (idempotente)
    def test_t4_idempotent(self):
        aux = self._center("Aux", "auxiliary")
        z = self._center("Z", "final")
        y = self._center("Y", "final")
        self._cost(aux, 3600)
        self._service(aux, z, 60)
        self._service(aux, y, 60)
        run = self.Run.create({**self.period, "method": "direct"})
        run.action_compute()
        n1 = len(run.line_ids)
        z1 = self._line(run, z).cost_received
        run.action_compute()
        self.assertEqual(len(run.line_ids), n1)
        self.assertEqual(self._line(run, z).cost_received, z1)

    # T6 — centro da chiudere senza base di ribaltamento → bloccato
    def test_t6_unclosable(self):
        aux = self._center("Aux senza base", "auxiliary")
        self._center("Z", "final")
        self._cost(aux, 1000)  # ha costo ma nessun servizio erogato
        run = self.Run.create({**self.period, "method": "direct"})
        with self.assertRaises(ValidationError):
            run.action_compute()

    # Gate — un utente Controlling (non manager) deve poter eseguire il run
    def test_user_can_compute(self):
        user = new_test_user(
            self.env,
            login="cmc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
        )
        aux = self._center("Aux", "auxiliary")
        z = self._center("Z", "final")
        y = self._center("Y", "final")
        self._cost(aux, 3600)
        self._service(aux, z, 60)
        self._service(aux, y, 60)
        run = self.Run.create({**self.period, "method": "direct"})
        run.with_user(user).action_compute()
        self.assertTrue(run.line_ids)
        self.assertEqual(run.state, "done")

    # Gate — reciproco senza alcun path verso un centro finale → bloccato
    def test_reciprocal_no_final_path(self):
        aux1 = self._center("Aux1", "auxiliary")
        aux2 = self._center("Aux2", "auxiliary")
        self._center("Z", "final")  # esiste ma non riceve servizi
        self._cost(aux1, 3600)
        self._cost(aux2, 1000)
        self._service(aux1, aux2, 50)
        self._service(aux2, aux1, 50)
        run = self.Run.create({**self.period, "method": "reciprocal"})
        with self.assertRaises(ValidationError):
            run.action_compute()
