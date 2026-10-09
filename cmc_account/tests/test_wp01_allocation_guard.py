"""WP-01 / ALC-01 - C-5 guard (a): the cmc_account classification engine leaves allocation lines alone.

CONTRACT (dispatch sec. 2.3, cmc_account side)
- Lines posted by an allocation (``cmc_allocation_id`` set) keep the classification of their run:
  ``_cmc_auto_classify`` skips them (no rule / account mapping / bridge fill, no ``cmc_bridge_source``).
- ``_cmc_classification_gate`` does not take them: a Controlling User writing a classification field on
  such a line falls through to the cmc_base lock -> UserError (NOT an AccessError, NOT a silent sudo write).
- ``create`` manual-marking ignores them: classification values given at creation (by the run, under the
  sentinel) do not set ``cmc_classified_manually`` nor persist a ``cmc.manual.classification`` override.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

DAY = date(2031, 3, 15)


@tagged("post_install", "-at_install")
class TestAllocationClassificationGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Rule = cls.env["cmc.classification.rule"]
        cls.Element = cls.env["cmc.cost.element"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.source = cls._center("WP01 G source")
        cls.target = cls._center("WP01 G target")
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "WP01 G m2", "code": "WP01G_M2"})
        cls.alloc_element = cls.Element.create({"name": "WP01 G allocated rent", "code": "WP01G_ALLOC"})
        cls.rule_element = cls.Element.create({"name": "WP01 G rule element", "code": "WP01G_RULE"})
        # rules that WOULD match the posted lines (match on center: posted lines carry no GL account)
        for center in (cls.source, cls.target):
            cls.Rule.create({"name": "WP01 G rule %s" % center.name, "center_id": center.id,
                             "cmc_behavior": "variable", "cmc_directness": "direct",
                             "cmc_cost_element_id": cls.rule_element.id})
        cls.AL.create({"name": "rent", "account_id": cls.source.id, "amount": -1000.0, "date": DAY})
        cls.cmc_user = new_test_user(cls.env, login="wp01_guard_user", groups="base.group_user,cmc_base.group_cmc_user")

    @classmethod
    def _center(cls, name):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final",
                              "company_id": cls.company.id})

    def _allocation(self, element=None):
        return self.env["cmc.allocation"].create({
            "name": "WP01 G split", "source_account_id": self.source.id, "driver_id": self.driver.id,
            "amount": 1000.0, "date": DAY, "element_id": element.id if element else False,
            "line_ids": [Command.create({"target_account_id": self.target.id, "quantity": 1})],
        })

    def test_allocation_line_not_auto_classified(self):
        # C-5 (a): a matching rule does not reclassify the system lines, they keep the allocation's element
        alloc = self._allocation(self.alloc_element)
        alloc.action_compute()
        posted = alloc.posted_line_ids
        self.assertEqual(len(posted), 2)
        # sanity: the rule does match an ordinary line on the same center
        ordinary = self.AL.create({"name": "ordinary", "account_id": self.target.id, "amount": -5.0, "date": DAY})
        self.assertEqual(ordinary.cmc_behavior, "variable")
        self.assertRecordValues(posted, [
            {"cmc_cost_element_id": self.alloc_element.id, "cmc_behavior": "fixed", "cmc_directness": "indirect",
             "cmc_classified_manually": False, "cmc_bridge_source": False},
        ] * 2)

    def test_allocation_without_element_not_filled_by_rule(self):
        # C-5 (a): an allocation with no element posts lines with no element; the fill-only rule stays out
        alloc = self._allocation()
        alloc.action_compute()
        self.assertRecordValues(alloc.posted_line_ids, [
            {"cmc_cost_element_id": False, "cmc_behavior": "fixed", "cmc_classified_manually": False,
             "cmc_bridge_source": False},
        ] * 2)

    def test_cmc_user_cannot_classify_allocation_line(self):
        # C-5 (a): the classification gate does not sudo-write a system line; the cmc_base lock refuses it
        alloc = self._allocation(self.alloc_element)
        alloc.action_compute()
        line = alloc.posted_line_ids.filtered(lambda posted: posted.account_id == self.target)
        with self.assertRaises(UserError) as caught:
            line.with_user(self.cmc_user).write({"cmc_behavior": "variable"})
        self.assertNotIsInstance(caught.exception, AccessError, "the allocation lock, not an ACL refusal")
        with self.assertRaises(UserError) as caught:
            line.with_user(self.cmc_user).write({"cmc_cost_element_id": self.rule_element.id})
        self.assertNotIsInstance(caught.exception, AccessError)
        self.assertRecordValues(line, [{"cmc_behavior": "fixed", "cmc_cost_element_id": self.alloc_element.id,
                                        "cmc_classified_manually": False}])

    def test_allocation_line_create_not_marked_manual(self):
        # C-5 (a): classification given at creation of a system line is the run's, not a manual override
        expense = self.env["account.account"].create({"name": "WP01 G rent", "code": "601977", "account_type": "expense"})
        bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.env["res.partner"].create({"name": "WP01 G landlord"}).id,
            "invoice_date": DAY,
            "date": DAY,
            "invoice_line_ids": [Command.create({"name": "rent", "quantity": 1.0, "price_unit": 100.0,
                                                 "account_id": expense.id, "tax_ids": [Command.clear()]})],
        })
        bill.action_post()
        move_line = bill.invoice_line_ids
        alloc = self._allocation(self.alloc_element)
        # what the run does: create under the sentinel, with classification fields set
        line = self.AL.with_context(cmc_run_state=CMC_RUN_STATE).create({
            "name": "system", "date": DAY, "account_id": self.target.id, "amount": -100.0,
            "move_line_id": move_line.id, "cmc_allocation_id": alloc.id,
            "cmc_cost_element_id": self.alloc_element.id, "cmc_behavior": "fixed",
        })
        self.assertFalse(line.cmc_classified_manually)
        self.assertFalse(self.env["cmc.manual.classification"].search([("move_line_id", "=", move_line.id)]))
