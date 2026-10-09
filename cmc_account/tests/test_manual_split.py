from odoo import Command, fields
from odoo.exceptions import UserError
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

    # the context key is settable over RPC: it must not switch the manual
    # flag off for a user write (only the in-process sentinel does)
    def test_client_context_cannot_skip_the_manual_flag(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        line_x = self._line_of(bill, self.center_x)
        line_x.with_user(self.controller).with_context(cmc_auto_classify=True).write({
            "cmc_cost_element_id": self.el_x.id,
            "cmc_behavior": "fixed",
        })
        self.assertTrue(line_x.cmc_classified_manually)
        new = self.env["account.analytic.line"].with_user(self.controller).with_context(
            cmc_auto_classify=True
        ).create({
            "name": "x", "account_id": self.center_y.id, "amount": -5.0,
            "cmc_behavior": "fixed",
        })
        self.assertTrue(new.cmc_classified_manually)

    def test_client_context_cannot_classify_an_excluded_line(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        line_x = self._line_of(bill, self.center_x)
        line_x.write({"cmc_excluded": True})  # system (superuser) flag
        with self.assertRaises(UserError):
            line_x.with_user(self.controller).with_context(
                cmc_auto_classify=True
            ).write({"cmc_behavior": "fixed"})

    # ------------------------------------------------------------------
    # the store: multi-plan splits, clearing a field, resetting, draft edits
    # ------------------------------------------------------------------

    def _manual_records(self, bill, center):
        return self.env["cmc.manual.classification"].search([
            ("move_line_id", "in", bill.sudo().line_ids.ids),
            ("center_id", "=", center.id),
        ])

    # two analytic lines of the same journal item can share the cost center when
    # the distribution spans several plans: same key = same classification
    def test_multi_plan_lines_sharing_the_center_classify_together(self):
        plan2 = self.env["account.analytic.plan"].create({"name": "Progetti SPLIT"})
        proj_a, proj_b = self.AA.create([
            {"name": "Progetto A", "plan_id": plan2.id},
            {"name": "Progetto B", "plan_id": plan2.id},
        ])
        bill = self._post_bill({
            "%s,%s" % (self.center_x.id, proj_a.id): 50,
            "%s,%s" % (self.center_x.id, proj_b.id): 50,
        })
        lines = bill.sudo().line_ids.analytic_line_ids
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines.cmc_center_id, self.center_x)

        lines.with_user(self.controller).write({
            "cmc_cost_element_id": self.el_x.id, "cmc_behavior": "semi_variable",
        })

        self.assertEqual(len(self._manual_records(bill, self.center_x)), 1)
        self._repost(bill)
        new_lines = bill.sudo().line_ids.analytic_line_ids
        self.assertEqual(len(new_lines), 2)
        self.assertEqual(new_lines.mapped("cmc_cost_element_id"), self.el_x)
        self.assertTrue(all(new_lines.mapped("cmc_classified_manually")))

    # emptying a field on a manual line is a manual choice too
    def test_clearing_a_field_of_a_manual_line_survives_repost(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        self._classify_by_hand(self._line_of(bill, self.center_x), self.el_x, "semi_variable", "direct")
        self._line_of(bill, self.center_x).with_user(self.controller).write({"cmc_behavior": False})

        self._repost(bill)

        self.assertRecordValues(self._line_of(bill, self.center_x), [{
            "cmc_classified_manually": True,
            "cmc_cost_element_id": self.el_x.id,
            "cmc_behavior": False,
            "cmc_directness": "direct",
        }])

    # "remove manual" is a true reset: back to what the rules say, for good
    def test_removing_the_manual_flag_resets_to_the_rules(self):
        bill = self._post_bill({str(self.center_x.id): 100})
        self._classify_by_hand(self._line_of(bill, self.center_x), self.el_x, "semi_variable", "direct")

        self._line_of(bill, self.center_x).with_user(self.controller).write(
            {"cmc_classified_manually": False}
        )

        expected = {
            "cmc_classified_manually": False,
            "cmc_cost_element_id": False,
            "cmc_behavior": "variable",
            "cmc_directness": "indirect",
        }
        self.assertRecordValues(self._line_of(bill, self.center_x), [expected])
        self.assertFalse(self._manual_records(bill, self.center_x))
        self._repost(bill)
        self.assertRecordValues(self._line_of(bill, self.center_x), [expected])

    # documented behaviour: the override belongs to (journal item, center)
    def test_draft_edit_keeps_the_override_per_center_and_orphans_the_old_one(self):
        bill = self._post_bill({str(self.center_x.id): 60, str(self.center_y.id): 40})
        self._classify_by_hand(self._line_of(bill, self.center_x), self.el_x, "semi_variable", "direct")
        accountant_bill = bill.with_user(self.accountant)
        accountant_bill.button_draft()
        accountant_bill.invoice_line_ids.analytic_distribution = {str(self.center_y.id): 100}
        accountant_bill.action_post()

        line_y = self._line_of(bill, self.center_y)
        self.assertFalse(line_y.cmc_classified_manually, "Y must not inherit X's override")
        self.assertEqual(line_y.cmc_behavior, "variable")
        self.assertTrue(self._manual_records(bill, self.center_x), "the old record is left orphaned")
