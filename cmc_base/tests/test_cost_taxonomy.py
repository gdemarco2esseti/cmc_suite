from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCostTaxonomy(TransactionCase):
    """TS-001 — Tassonomia dei costi e oggetto di costo generalizzato."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AnalyticLine = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Center Manutenzione", "plan_id": cls.plan.id}
        )
        cls.driver = cls.env["cmc.cost.driver"].create(
            {"name": "Unità prodotte", "code": "UNITS"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Cliente X"})

    def _make_line(self, **vals):
        base = {"name": "costo", "amount": -100.0, "account_id": self.center.id}
        base.update(vals)
        return self.AnalyticLine.create(base)

    # T1 — happy path: costo variabile con driver
    def test_t1_variable_with_driver(self):
        line = self._make_line(
            cmc_behavior="variable", cmc_driver_id=self.driver.id, cmc_directness="direct"
        )
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_driver_id, self.driver)
        self.assertEqual(line.cmc_directness, "direct")

    # T2 — costo fisso
    def test_t2_fixed(self):
        line = self._make_line(cmc_behavior="fixed")
        self.assertEqual(line.cmc_behavior, "fixed")

    # T3 — semivariabile con area di rilevanza valida
    def test_t3_semivariable_relevant_range(self):
        line = self._make_line(
            cmc_behavior="semi_variable",
            cmc_relevance_min=1000.0,
            cmc_relevance_max=5000.0,
        )
        self.assertEqual(line.cmc_behavior, "semi_variable")
        self.assertEqual(line.cmc_relevance_min, 1000.0)
        self.assertEqual(line.cmc_relevance_max, 5000.0)

    # T3-edge — area di rilevanza incoerente (max < min) deve essere bloccata
    def test_t3_invalid_range_blocked(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._make_line(
                    cmc_behavior="semi_variable",
                    cmc_relevance_min=5000.0,
                    cmc_relevance_max=1000.0,
                )
                self.env.flush_all()

    # T3-bis — solo limite inferiore impostato: deve essere AMMESSO
    # (Float Odoo = NOT NULL default 0.0; 0 = limite non impostato)
    def test_t3_min_only_allowed(self):
        line = self._make_line(
            cmc_behavior="semi_variable", cmc_relevance_min=1000.0
        )
        self.assertEqual(line.cmc_relevance_min, 1000.0)
        self.assertEqual(line.cmc_relevance_max, 0.0)

    # T3-ter — solo limite superiore impostato: deve essere AMMESSO
    def test_t3_max_only_allowed(self):
        line = self._make_line(
            cmc_behavior="semi_variable", cmc_relevance_max=5000.0
        )
        self.assertEqual(line.cmc_relevance_max, 5000.0)

    # Guard — oggetto di costo vuoto/multi non deve rompere la classificazione
    def test_guard_empty_cost_object(self):
        line = self._make_line(
            cmc_behavior="variable",
            cmc_cost_object_ref=f"account.analytic.account,{self.center.id}",
        )
        empty = self.env["res.partner"]
        self.assertEqual(line.cmc_classify_for(empty), "common")
        multi = self.partner | self.env["res.partner"].create({"name": "Y"})
        self.assertEqual(line.cmc_classify_for(multi), "common")

    # Whitelist — un modello fuori dalla selezione non produce classificazione
    def test_traceability_whitelist_only(self):
        line = self._make_line(
            cmc_behavior="variable",
            cmc_cost_object_ref=f"account.analytic.account,{self.center.id}",
        )
        off_list = line.with_context(cmc_cost_object=f"res.users,{self.env.uid}")
        self.assertFalse(off_list.cmc_cost_traceability)

    # T4 — oggetto di costo per centro (Reference verso analytic account)
    def test_t4_cost_object_reference(self):
        line = self._make_line(
            cmc_behavior="fixed",
            cmc_cost_object_ref=f"account.analytic.account,{self.center.id}",
        )
        self.assertEqual(line.cmc_cost_object_ref, self.center)

    # T6 — speciale/comune RELATIVO all'oggetto di costo
    def test_t6_traceability_is_relative(self):
        line = self._make_line(
            cmc_behavior="variable",
            cmc_cost_object_ref=f"account.analytic.account,{self.center.id}",
        )
        # metodo pubblico: speciale per il proprio centro, comune per il cliente
        self.assertEqual(line.cmc_classify_for(self.center), "special")
        self.assertEqual(line.cmc_classify_for(self.partner), "common")
        # campo context-relative: stessa riga, due verità a seconda del contesto
        as_center = line.with_context(
            cmc_cost_object=f"account.analytic.account,{self.center.id}"
        )
        as_partner = line.with_context(
            cmc_cost_object=f"res.partner,{self.partner.id}"
        )
        self.assertEqual(as_center.cmc_cost_traceability, "special")
        self.assertEqual(as_partner.cmc_cost_traceability, "common")

    # T7 — uso a valle senza classificazione: il sistema avvisa
    def test_t7_unclassified_raises(self):
        line = self._make_line()  # nessun cmc_behavior
        self.assertFalse(line.cmc_behavior)
        with self.assertRaises(UserError):
            line.cmc_ensure_classified()
