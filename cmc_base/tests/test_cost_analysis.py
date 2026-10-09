from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCostAnalysis(TransactionCase):
    """TS-008 — Quadro analisi costi (matrice centri x elementi, pre/post)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        cls.el_mod = cls.env["cmc.cost.element"].create({"name": "Manodopera", "code": "MOD"})
        cls.el_mat = cls.env["cmc.cost.element"].create({"name": "Materie", "code": "MAT"})
        cls.ribalt = cls.env.ref("cmc_base.cost_element_ribaltamento")

        cls.manut = cls._center("Manutenzione", "auxiliary")
        cls.z = cls._center("Montaggio Z", "final")
        cls.y = cls._center("Montaggio Y", "final")
        cls._cost(cls.manut, 3600, cls.el_mod)
        cls._cost(cls.z, 1000, cls.el_mat)
        cls._cost(cls.y, 600, cls.el_mat)
        cls._service(cls.manut, cls.z, 60)
        cls._service(cls.manut, cls.y, 60)

        cls.crun = cls.env["cmc.closing.run"].create(
            {"name": "Chiusura", "method": "direct",
             "date_from": date(date.today().year, 1, 1), "date_to": date(date.today().year, 12, 31)}
        )
        cls.crun.action_compute()

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype}
        )

    @classmethod
    def _cost(cls, center, amount, element):
        cls.AL.create(
            {"name": "c", "account_id": center.id, "amount": -amount,  # AD-005: cost = negative amount
             "cmc_behavior": "fixed", "cmc_cost_element_id": element.id,
             "date": date(date.today().year, 6, 15)}
        )

    @classmethod
    def _service(cls, p, r, qty):
        cls.env["cmc.center.service"].create(
            {"provider_id": p.id, "receiver_id": r.id,
             "driver_id": cls.driver.id, "quantity": qty}
        )

    def _pre(self):
        return self.crun.cost_analysis_ids.filtered(lambda l: l.state == "pre")

    def _post(self):
        return self.crun.cost_analysis_ids.filtered(lambda l: l.state == "post")

    # T1 — la matrice quadra: totale pre = somma di tutti i costi (5200)
    def test_t1_pre_total(self):
        self.assertEqual(sum(self._pre().mapped("amount")), 5200.0)

    # T1 — conservazione: totale post = totale pre
    def test_t1_post_conserves(self):
        self.assertEqual(sum(self._post().mapped("amount")), 5200.0)

    # T2 — pre vs post: l'ausiliario ha costo pre, va a 0 post
    def test_t2_auxiliary_emptied(self):
        pre_manut = sum(
            self._pre().filtered(lambda l: l.center_id == self.manut).mapped("amount")
        )
        post_manut = sum(
            self._post().filtered(lambda l: l.center_id == self.manut).mapped("amount")
        )
        self.assertEqual(pre_manut, 3600.0)
        self.assertEqual(post_manut, 0.0)

    # T2 — il centro finale assorbe il ribaltamento come elemento dedicato
    def test_t2_final_absorbs_ribaltamento(self):
        post_z = self._post().filtered(lambda l: l.center_id == self.z)
        by_el = {l.element_id: l.amount for l in post_z}
        self.assertEqual(by_el[self.el_mat], 1000.0)        # costo proprio
        self.assertEqual(by_el[self.ribalt], 1800.0)        # ribaltamento ausiliari
        self.assertEqual(sum(post_z.mapped("amount")), 2800.0)

    # TS-009 — il report PDF (consuntivo) renderizza senza errori
    def test_report_renders(self):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "cmc_base.action_report_cmc_consuntivo", self.crun.ids
        )
        self.assertIn(b"Cost Closing", html)
