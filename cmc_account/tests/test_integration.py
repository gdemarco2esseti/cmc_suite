from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAccountBridgeIntegration(TransactionCase):
    """Fase 1.5a — flussi reali: ciclo bozza/ripubblicazione, ACL, multi-company,
    ri-classificazione in scrittura e batching delle write."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.AA = cls.env["account.analytic.account"]
        cls.Rule = cls.env["cmc.classification.rule"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create(
            {"name": "Centro INT", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.expense = cls.env["account.account"].create(
            {"name": "Servizi INT", "code": "601100", "account_type": "expense"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Fornitore INT"})

    def _bill(self, env=None, amount=100.0):
        return (env or self.env)["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.context_today(self.env.user),
            "date": fields.Date.context_today(self.env.user),
            "invoice_line_ids": [Command.create({
                "name": "servizio",
                "quantity": 1.0,
                "price_unit": amount,
                "account_id": self.expense.id,
                "tax_ids": [Command.clear()],
                "analytic_distribution": {str(self.center.id): 100},
            })],
        })

    # --- item 10: la classificazione manuale sopravvive a bozza + ripubblicazione
    def test_manual_classification_survives_draft_and_repost(self):
        bill = self._bill()
        bill.action_post()
        line = bill.line_ids.analytic_line_ids
        self.assertEqual(len(line), 1)
        line.cmc_behavior = "fixed"
        self.assertTrue(line.cmc_classified_manually)

        bill.button_draft()
        self.assertFalse(line.exists(), "core unlinks the analytic line on draft")
        bill.action_post()

        new_line = bill.line_ids.analytic_line_ids
        self.assertEqual(len(new_line), 1)
        self.assertNotEqual(new_line.id, line.id)
        self.assertEqual(new_line.cmc_behavior, "fixed")
        self.assertTrue(new_line.cmc_classified_manually)

    # --- item 11: la classificazione si ri-innesca anche in scrittura
    def test_classification_retriggered_on_write(self):
        plain = self.env["account.account"].create(
            {"name": "Neutro", "code": "699100", "account_type": "expense"}
        )
        line = self.AL.create({
            "name": "c", "account_id": self.center.id,
            "general_account_id": plain.id, "amount": -100.0,
        })
        self.assertFalse(line.cmc_behavior)
        self.Rule.create({
            "name": "r", "general_account_id": self.expense.id,
            "cmc_behavior": "variable",
        })
        line.general_account_id = self.expense
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertFalse(line.cmc_classified_manually)

    # --- item 12: una regola senza piu' criteri non deve sopravvivere
    def test_rule_dies_with_its_only_criterion(self):
        account = self.env["account.account"].create(
            {"name": "Temporaneo", "code": "699200", "account_type": "expense"}
        )
        rule = self.Rule.create({
            "name": "solo conto", "general_account_id": account.id,
            "cmc_behavior": "fixed",
        })
        account.unlink()
        self.assertFalse(rule.exists(), "a criterion-less rule would match every line")
        other = self.env["account.account"].create(
            {"name": "Altro conto", "code": "699300", "account_type": "expense"}
        )
        line = self.AL.create({
            "name": "c", "account_id": self.center.id,
            "general_account_id": other.id, "amount": -10.0,
        })
        self.assertFalse(line.cmc_behavior)

    # --- item 13: write() non deve mutare il dict del chiamante
    def test_write_does_not_mutate_caller_vals(self):
        line = self.AL.create({
            "name": "c", "account_id": self.center.id, "amount": -10.0,
        })
        vals = {"cmc_behavior": "fixed"}
        line.write(vals)
        self.assertEqual(vals, {"cmc_behavior": "fixed"})

    # --- item 17: cmc_behavior esplicitamente False non e' classificazione manuale
    def test_explicit_false_is_not_manual(self):
        line = self.AL.create({
            "name": "c", "account_id": self.center.id, "amount": -10.0,
            "cmc_behavior": False,
        })
        self.assertFalse(line.cmc_classified_manually)

    # --- ACL: un contabile SENZA diritti CMC classifica comunque (niente sudo)
    def test_accountant_without_cmc_rights_triggers_classification(self):
        self.Rule.create({
            "name": "servizi", "general_account_id": self.expense.id,
            "cmc_behavior": "variable", "cmc_directness": "indirect",
        })
        accountant = new_test_user(
            self.env, login="cmc_accountant",
            groups="base.group_user,account.group_account_user",
        )
        self.assertFalse(accountant.has_group("cmc_base.group_cmc_user"))
        bill = self._bill(env=self.env(user=accountant))
        bill.action_post()
        line = bill.line_ids.analytic_line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.cmc_behavior, "variable")

    # --- multi-company: una regola di B non tocca mai una riga di A
    def test_rule_of_other_company_never_classifies(self):
        company_b = self.env["res.company"].create({"name": "CMC Company B"})
        shared_center = self.AA.create({
            "name": "Centro condiviso", "plan_id": self.plan.id,
            "cmc_center_type": "final", "company_id": False,
        })
        self.Rule.create({
            "name": "solo B", "company_id": company_b.id,
            "partner_id": self.partner.id, "cmc_behavior": "fixed",
        })
        line_a = self.AL.create({
            "name": "a", "account_id": shared_center.id,
            "partner_id": self.partner.id, "amount": -10.0,
            "company_id": self.env.company.id,
        })
        self.assertFalse(line_a.cmc_behavior, "company B rule leaked into company A")
        line_b = self.AL.with_company(company_b).create({
            "name": "b", "account_id": shared_center.id,
            "partner_id": self.partner.id, "amount": -10.0,
            "company_id": company_b.id,
        })
        self.assertEqual(line_b.cmc_behavior, "fixed")

    # --- item 21: la classificazione di un lotto costa write RAGGRUPPATE, non 1/riga
    def test_classification_writes_are_batched(self):
        self.Rule.create({
            "name": "batch", "general_account_id": self.expense.id,
            "cmc_behavior": "variable", "cmc_directness": "direct",
        })
        vals_list = [{
            "name": "c%s" % i, "account_id": self.center.id,
            "general_account_id": self.expense.id, "amount": -1.0,
            "date": fields.Date.context_today(self.env.user) - relativedelta(days=1),
        } for i in range(100)]
        # Upper bound. Almost all of these queries are core's (hr_timesheet
        # pre-processes one employee lookup per row): the classification engine
        # itself adds a constant handful. Going back to a per-record write would
        # add ~100 queries here -- ~300 with the three bridges chained -- because
        # cmc_base re-syncs capacity rows on every analytic-line write.
        with self.assertQueryCount(default=130):
            self.AL.create(vals_list)
