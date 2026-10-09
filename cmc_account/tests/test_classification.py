from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestClassification(TransactionCase):
    """Fase 1.5a — mapping conto->elemento + auto-classificazione (fill-only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Rule = cls.env["cmc.classification.rule"]
        cls.el_mod = cls.env["cmc.cost.element"].create({"name": "MOD", "code": "MOD"})
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create(
            {"name": "Centro", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.acc = cls.env["account.account"].create(
            {"name": "Servizi", "code": "600100", "account_type": "expense",
             "cmc_cost_element_id": cls.el_mod.id}
        )
        cls.acc_plain = cls.env["account.account"].create(
            {"name": "Altro", "code": "699999", "account_type": "expense"}
        )

    def _line(self, account, **kw):
        vals = {"name": "c", "account_id": self.center.id,
                "general_account_id": account.id, "amount": -100}
        vals.update(kw)
        return self.AL.create(vals)

    # TS-INT-MAP + RULE — elemento da mapping conto + comportamento da regola
    def test_mapping_and_rule(self):
        self.Rule.create(
            {"name": "r", "general_account_id": self.acc.id,
             "cmc_behavior": "variable", "cmc_directness": "direct"}
        )
        line = self._line(self.acc)
        self.assertEqual(line.cmc_cost_element_id, self.el_mod)
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_directness, "direct")

    # TS-INT-RULE T3 — nessuna regola/mapping -> resta non classificata
    def test_unclassified_when_no_rule(self):
        line = self._line(self.acc_plain)
        self.assertFalse(line.cmc_behavior)
        self.assertFalse(line.cmc_cost_element_id)

    # TS-INT-RULE T4 / INT-EXC T4 — classificazione manuale NON sovrascritta
    def test_manual_not_overwritten(self):
        self.Rule.create(
            {"name": "r", "general_account_id": self.acc.id, "cmc_behavior": "variable"}
        )
        line = self._line(self.acc, cmc_behavior="fixed")
        self.assertTrue(line.cmc_classified_manually)
        self.assertEqual(line.cmc_behavior, "fixed")  # rule did not overwrite
        # re-apply must not touch it either
        line._cmc_auto_classify()
        self.assertEqual(line.cmc_behavior, "fixed")

    # TS-INT-RULE T2 — priorità: la regola a sequence minore vince
    def test_priority(self):
        self.Rule.create(
            {"name": "lo", "sequence": 5, "general_account_id": self.acc.id,
             "cmc_behavior": "variable"}
        )
        self.Rule.create(
            {"name": "hi", "sequence": 10, "general_account_id": self.acc.id,
             "cmc_behavior": "fixed"}
        )
        line = self._line(self.acc)
        self.assertEqual(line.cmc_behavior, "variable")

    # TS-INT-RULE T5 — ri-applicazione massiva su righe non manuali
    def test_reapply_after_rule_added(self):
        line = self._line(self.acc_plain)
        self.assertFalse(line.cmc_behavior)
        self.Rule.create(
            {"name": "r", "general_account_id": self.acc_plain.id,
             "cmc_behavior": "fixed"}
        )
        line._cmc_auto_classify()
        self.assertEqual(line.cmc_behavior, "fixed")

    # TS-INT-FIN T2 — nessuna doppia imputazione (si classifica, non si crea)
    def test_no_double_imputation(self):
        self.Rule.create(
            {"name": "r", "general_account_id": self.acc.id, "cmc_behavior": "variable"}
        )
        before = self.AL.search_count([("account_id", "=", self.center.id)])
        self._line(self.acc)
        after = self.AL.search_count([("account_id", "=", self.center.id)])
        self.assertEqual(after, before + 1)  # exactly one line, not duplicated

    def test_unclassified_queue_opens_on_its_cost_list(self):
        # with hr_timesheet installed the default list is the timesheet one
        action = self.env.ref("cmc_account.action_cmc_unclassified_lines")
        list_view = action.view_ids.filtered(lambda v: v.view_mode == "list").view_id
        self.assertEqual(list_view, self.env.ref("cmc_account.view_cmc_unclassified_list"))
        arch = self.env["account.analytic.line"].get_views([(list_view.id, "list")])["views"]["list"]["arch"]
        self.assertIn('name="cmc_cost_amount"', arch)
