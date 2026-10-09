from odoo import Command, fields
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestManualClassificationSplit(TransactionCase):
    """AC5 — la classificazione manuale di UNA riga analitica non deve
    propagarsi alle righe sorelle della stessa riga contabile (move line ->
    righe analitiche e' 1:N con una analytic_distribution ripartita)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.Rule = cls.env["cmc.classification.rule"]
        cls.Element = cls.env["cmc.cost.element"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center_x = cls.AA.create(
            {"name": "Centro X", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.center_y = cls.AA.create(
            {"name": "Centro Y", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.expense = cls.env["account.account"].create(
            {"name": "Servizi SPLIT", "code": "601900", "account_type": "expense"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Fornitore SPLIT"})
        cls.el_x = cls.Element.create({"name": "Elemento X", "code": "SPLX"})
        cls.el_y = cls.Element.create({"name": "Elemento Y", "code": "SPLY"})
        # rule valid for every line of the bill: what Y must fall back to
        cls.Rule.create({
            "name": "servizi split", "general_account_id": cls.expense.id,
            "cmc_behavior": "variable", "cmc_directness": "indirect",
        })
        # AC5.4 — the controller only has CMC rights (which imply analytic
        # accounting), no accounting group; the accountant has billing rights
        # only, no CMC group.
        cls.plain_controller = new_test_user(
            cls.env, login="cmc_split_plain_controller",
            groups="base.group_user,cmc_base.group_cmc_user",
        )
        # why the extra groups: with hr_timesheet installed (cmc_suite pulls it
        # in through cmc_hr_timesheet) core refuses ANY write on an analytic
        # line whose user_id is someone else unless the writer is a timesheet
        # approver (hr_timesheet _check_can_write); and the approver's record
        # rule only covers project lines, so writing a vendor-bill line also
        # needs the Invoicing rule [(1, '=', 1)]. That ACL gap is pinned
        # separately by test_controller_with_cmc_rights_only_classifies_bill_line;
        # here the controller gets the groups so AC5.1-5.3 exercise the split
        # defect and nothing else.
        controller_groups = "base.group_user,cmc_base.group_cmc_user"
        if cls.env.ref("hr_timesheet.group_hr_timesheet_approver", raise_if_not_found=False):
            controller_groups += ",hr_timesheet.group_hr_timesheet_approver,account.group_account_invoice"
        cls.controller = new_test_user(
            cls.env, login="cmc_split_controller", groups=controller_groups,
        )
        cls.accountant = new_test_user(
            cls.env, login="cmc_split_accountant",
            groups="base.group_user,account.group_account_invoice",
        )

    def _post_bill(self, distribution):
        env = self.env(user=self.accountant)
        bill = env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.context_today(self.env.user),
            "date": fields.Date.context_today(self.env.user),
            "invoice_line_ids": [Command.create({
                "name": "servizio ripartito",
                "quantity": 1.0,
                "price_unit": 100.0,
                "account_id": self.expense.id,
                "tax_ids": [Command.clear()],
                "analytic_distribution": distribution,
            })],
        })
        bill.action_post()
        return bill

    def _repost(self, bill):
        bill = bill.with_user(self.accountant)
        bill.button_draft()
        bill.action_post()

    def _line_of(self, bill, center):
        lines = bill.sudo().line_ids.analytic_line_ids.filtered(
            lambda line: line.account_id == center
        )
        self.assertEqual(len(lines), 1, "one analytic line per center expected")
        return lines

    def _classify_by_hand(self, line, element, behavior, directness, user=None):
        # what the UI does: one write of the classification fields
        line.with_user(user or self.controller).write({
            "cmc_cost_element_id": element.id,
            "cmc_behavior": behavior,
            "cmc_directness": directness,
        })
        self.assertTrue(line.cmc_classified_manually)

    # AC5.1 — manuale solo su X: dopo reset+repost Y NON eredita i valori di X.
    # Behavior deliberately NOT 'fixed': a leaked 'fixed' crashes the repost in
    # cmc_base capacity sync (pinned by the next test), which would hide the
    # leak behind a ValueError.
    def test_manual_on_one_split_line_does_not_leak_to_sibling(self):
        bill = self._post_bill({str(self.center_x.id): 60, str(self.center_y.id): 40})
        line_x = self._line_of(bill, self.center_x)
        line_y = self._line_of(bill, self.center_y)
        self.assertEqual(line_y.cmc_behavior, "variable", "precondition: Y classified by rule")
        self._classify_by_hand(line_x, self.el_x, "semi_variable", "direct")
        self.assertFalse(line_y.cmc_classified_manually)

        self._repost(bill)

        new_x = self._line_of(bill, self.center_x)
        new_y = self._line_of(bill, self.center_y)
        self.assertNotEqual(new_x, line_x)
        self.assertRecordValues(new_x, [{
            "cmc_classified_manually": True,
            "cmc_cost_element_id": self.el_x.id,
            "cmc_behavior": "semi_variable",
            "cmc_directness": "direct",
        }])
        self.assertRecordValues(new_y, [{
            "cmc_classified_manually": False,
            "cmc_cost_element_id": False,
            "cmc_behavior": "variable",
            "cmc_directness": "indirect",
        }])

    # AC5.1 (fixed) — un manuale 'fixed' su X non deve bloccare la ripubblicazione
    # della fattura: oggi Y lo eredita e la write a 2 righe 'fixed' su centri
    # diversi rompe la sync capacita' di cmc_base (Expected singleton).
    def test_manual_fixed_on_one_split_line_does_not_break_repost(self):
        bill = self._post_bill({str(self.center_x.id): 60, str(self.center_y.id): 40})
        self._classify_by_hand(self._line_of(bill, self.center_x), self.el_x, "fixed", "direct")

        self._repost(bill)

        self.assertEqual(bill.state, "posted")
        self.assertEqual(self._line_of(bill, self.center_x).cmc_behavior, "fixed")
        self.assertEqual(self._line_of(bill, self.center_y).cmc_behavior, "variable")

    # AC5.2 — manuali diverse su X e Y: entrambe sopravvivono, ognuna sulla sua riga
    def test_two_different_manual_split_lines_both_survive(self):
        bill = self._post_bill({str(self.center_x.id): 60, str(self.center_y.id): 40})
        self._classify_by_hand(self._line_of(bill, self.center_x), self.el_x, "fixed", "direct")
        self._classify_by_hand(self._line_of(bill, self.center_y), self.el_y, "semi_variable", "indirect")

        self._repost(bill)

        self.assertRecordValues(self._line_of(bill, self.center_x), [{
            "cmc_classified_manually": True,
            "cmc_cost_element_id": self.el_x.id,
            "cmc_behavior": "fixed",
            "cmc_directness": "direct",
        }])
        self.assertRecordValues(self._line_of(bill, self.center_y), [{
            "cmc_classified_manually": True,
            "cmc_cost_element_id": self.el_y.id,
            "cmc_behavior": "semi_variable",
            "cmc_directness": "indirect",
        }])

    # AC5.3 — distribuzione 100%: il manuale sopravvive a reset+repost (non regredire)
    def test_manual_on_full_distribution_survives_repost(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        line_x = self._line_of(bill, self.center_x)
        self._classify_by_hand(line_x, self.el_x, "fixed", "direct")

        self._repost(bill)

        new_x = self._line_of(bill, self.center_x)
        self.assertNotEqual(new_x, line_x)
        self.assertRecordValues(new_x, [{
            "cmc_classified_manually": True,
            "cmc_cost_element_id": self.el_x.id,
            "cmc_behavior": "fixed",
            "cmc_directness": "direct",
        }])

    # AC5.4 — un controller con i SOLI diritti CMC classifica a mano la riga
    # analitica di una fattura fornitore registrata da un contabile
    def test_controller_with_cmc_rights_only_classifies_bill_line(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        line_x = self._line_of(bill, self.center_x)
        self._classify_by_hand(line_x, self.el_x, "fixed", "direct", user=self.plain_controller)
        self.assertEqual(line_x.cmc_cost_element_id, self.el_x)
