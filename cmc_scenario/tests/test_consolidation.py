"""WP-11 INT-04 - child versions consolidated into a parent: sum of the lines per center x element x month in
company currency, children approved and protected while the consolidation is computed. CONTRACT: see common.py.

Hand case: child 1 (company currency) center A Jan 10 x 100, var 6, fixed 200 -> 1,000 / -600 / -200, Feb
10 x 50, var 6, fixed 200 -> 500 / -300 / -200; child 2 (version currency, rate 2.0) center A Jan 5 x 100, var 3,
fixed 50 -> 1,000 / -600 / -100, center B Jan 1 x 10, var 0.5, fixed 1 -> 20 / -10 / -2.
Consolidated: A Jan 2,000 / -1,200 / -300; A Feb 500 / -300 / -200; B Jan 20 / -10 / -2; total 508.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import FEB, JAN, ScenarioCase


@tagged("post_install", "-at_install")
class TestConsolidation(ScenarioCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parent = cls._version("WP11 group plan", {})
        cls.child_1 = cls._version("WP11 division 1", {cls.center_a: (10.0, 100.0, 6.0, 200.0)})
        cls._facts(cls.child_1, {cls.center_a: (10.0, 50.0, 6.0, 200.0)}, month=FEB)
        cls.child_2 = cls._version("WP11 division 2", {
            cls.center_a: (5.0, 100.0, 3.0, 50.0),
            cls.center_b: (1.0, 10.0, 0.5, 1.0),
        }, currency_rate=2.0)
        (cls.child_1 | cls.child_2).action_approve()

    def test_sum_per_center_element_month_in_company_currency(self):
        # INT-04: child -> parent consolidation at the plan grain (AD-105 month)
        run = self._consolidation(self.parent, self.child_1 | self.child_2)
        run.action_compute()
        self.assertRecordValues(run, [{"state": "done", "total_amount": 508.0}])
        lines = self._by_key(run.line_ids)
        a, b = self.center_a, self.center_b
        expected = {
            (a, self.el_revenue, JAN): 2000.0, (a, self.el_variable, JAN): -1200.0, (a, self.el_fixed, JAN): -300.0,
            (a, self.el_revenue, FEB): 500.0, (a, self.el_variable, FEB): -300.0, (a, self.el_fixed, FEB): -200.0,
            (b, self.el_revenue, JAN): 20.0, (b, self.el_variable, JAN): -10.0, (b, self.el_fixed, JAN): -2.0,
        }
        self.assertEqual({key: line.amount for key, line in lines.items()}, expected)
        self.assertEqual(sorted(self.prepare_calls), sorted((self.child_1 | self.child_2).ids))

    def test_single_child_equals_child(self):
        # INT-04: consolidating one child reproduces it (conservation, INT-05)
        run = self._consolidation(self.parent, self.child_2)
        run.action_compute()
        self.assertEqual(run.total_amount, 2 * (500.0 - 300.0 - 50.0 + 10.0 - 5.0 - 1.0))
        self.assertEqual(len(run.line_ids), 6)

    def test_draft_child_refused(self):
        # INT-04 / AD-113: a consolidation reads approved versions only
        draft = self._version("WP11 division draft", {self.center_a: (1.0, 1.0, 0.0, 0.0)})
        run = self._consolidation(self.parent, self.child_1 | draft)
        with self.assertRaisesRegex(UserError, "WP11 division draft"):
            run.action_compute()

    def test_no_child_refused(self):
        run = self._consolidation(self.parent, self.env["cmc.plan.version"])
        with self.assertRaises(UserError):
            run.action_compute()

    def test_parent_cannot_be_its_own_child(self):
        # INT-04: a version cannot consolidate itself
        with self.assertRaises(ValidationError):
            self._consolidation(self.child_1, self.child_1 | self.child_2)

    def test_child_reset_blocked_while_consolidated(self):
        # AD-002: the consolidation reads the children; they cannot be reopened under it
        run = self._consolidation(self.parent, self.child_1 | self.child_2)
        run.action_compute()
        with self.assertRaises(UserError):
            self.child_1.with_user(self.manager).action_reset_to_draft()
        run.action_reset_to_draft()
        self.assertFalse(run.line_ids)
        self.assertEqual(run.total_amount, 0.0)
        self.child_1.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.child_1.state, "draft")

    def test_other_company_child_refused(self):
        # AD-115: consolidation stays inside one company
        company_b = self.env["res.company"].create({"name": "CMC WP11 Co B consolidation"})
        foreign = self._version("WP11 foreign division", {}, company=company_b)
        with self.assertRaises(UserError):
            self._consolidation(self.parent, self.child_1 | foreign)
