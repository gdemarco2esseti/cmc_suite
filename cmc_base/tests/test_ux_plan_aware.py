from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPlanAware(TransactionCase):
    """TS-UX-001 — i costi di un centro sono letti a prescindere dal piano."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        # closing runs always carry a period: the whole current year covers
        # the analytic lines below (dated today by default)
        cls.period = {"date_from": date(date.today().year, 1, 1), "date_to": date(date.today().year, 12, 31)}
        # a root plan whose cost column is NOT the default account_id
        roots = cls.env["account.analytic.plan"].search([("parent_id", "=", False)])
        cls.plan_alt = roots.filtered(lambda p: p._column_name() != "account_id")[:1]

    def _make_cost(self, center, amount, behavior="fixed", element=False):
        col = center.plan_id._column_name()
        vals = {"name": "c", col: center.id, "amount": amount, "cmc_behavior": behavior, "date": date(date.today().year, 6, 15)}
        if element:
            vals["cmc_cost_element_id"] = element.id
        return self.AL.create(vals)

    # T1 — costo su piano non-default visto dal ribaltamento
    def test_t1_closing_sees_non_default_plan(self):
        self.assertTrue(self.plan_alt, "serve un piano root non-default nel db di test")
        aux = self.AA.create(
            {"name": "Manut", "plan_id": self.plan_alt.id, "cmc_center_type": "auxiliary"}
        )
        z = self.AA.create(
            {"name": "Z", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        self._make_cost(aux, 1000)
        self.env["cmc.center.service"].create(
            {"provider_id": aux.id, "receiver_id": z.id,
             "driver_id": self.driver.id, "quantity": 10}
        )
        run = self.env["cmc.closing.run"].create({"name": "r", "method": "direct", **self.period})
        run.action_compute()
        zline = run.line_ids.filtered(lambda l: l.center_id == z)
        aline = run.line_ids.filtered(lambda l: l.center_id == aux)
        self.assertEqual(zline.cost_received, 1000.0)
        self.assertEqual(aline.cost_residual, 0.0)

    # T2 — il quadro analisi costi vede il costo su piano non-default
    def test_t2_cost_analysis_sees_non_default_plan(self):
        z = self.AA.create(
            {"name": "Zq", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        el = self.env["cmc.cost.element"].create({"name": "Mat", "code": "MZ"})
        self._make_cost(z, 700, element=el)
        run = self.env["cmc.closing.run"].create({"name": "rq", "method": "direct", **self.period})
        run.action_compute()
        pre = run.cost_analysis_ids.filtered(
            lambda l: l.center_id == z and l.state == "pre"
        )
        self.assertEqual(sum(pre.mapped("amount")), 700.0)

    # UX-GUIDE — il cockpit riflette lo stato del flusso
    def test_cockpit_status(self):
        Cockpit = self.env["cmc.cockpit"]
        base = next(
            s["count"] for s in Cockpit.get_cockpit_data()["pipeline"]
            if s["key"] == "centers"
        )
        self.AA.create(
            {"name": "Cck", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        data = Cockpit.get_cockpit_data()
        steps = {s["key"]: s for s in data["pipeline"]}
        self.assertEqual(steps["centers"]["state"], "done")
        self.assertEqual(steps["centers"]["count"], base + 1)
        self.assertEqual(steps["centers"]["num"], 1)  # sequential numbering
        self.assertNotEqual(steps["closing"]["state"], "done")

    # TS-UX-002 — il campo unico "Centro di costo" instrada sul piano giusto
    def test_cmc_center_id_routes_to_plan_column(self):
        root_def = self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        c1 = self.AA.create(
            {"name": "C1", "plan_id": root_def.id, "cmc_center_type": "final"}
        )
        l1 = self.AL.create(
            {"name": "x", "cmc_center_id": c1.id, "amount": 100, "cmc_behavior": "fixed"}
        )
        self.assertEqual(l1.account_id, c1)
        self.assertEqual(l1.cmc_center_id, c1)
        # center on a non-default plan -> its own plan column is set
        c2 = self.AA.create(
            {"name": "C2", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        l2 = self.AL.create(
            {"name": "y", "cmc_center_id": c2.id, "amount": 50, "cmc_behavior": "fixed"}
        )
        self.assertEqual(l2[self.plan_alt._column_name()], c2)
        self.assertEqual(l2.cmc_center_id, c2)

    # Gate fix — cambiando centro su un piano diverso, la vecchia colonna si pulisce
    def test_cmc_center_id_change_clears_old_column(self):
        root_def = self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        c_a = self.AA.create(
            {"name": "CA", "plan_id": root_def.id, "cmc_center_type": "final"}
        )
        c_b = self.AA.create(
            {"name": "CB", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        line = self.AL.create(
            {"name": "x", "cmc_center_id": c_a.id, "amount": 10, "cmc_behavior": "fixed"}
        )
        self.assertEqual(line.account_id, c_a)
        # reswap to a center on another plan
        line.cmc_center_id = c_b.id
        self.assertFalse(line.account_id)  # old column cleared
        self.assertEqual(line[self.plan_alt._column_name()], c_b)
        self.assertEqual(line.cmc_center_id, c_b)  # no ambiguity
