"""AD-115 / BUD-05 - budget security: user edits drafts, manager approves, responsible reads only his centers,
outsider sees nothing, two companies are isolated.
"""

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetSecurity(CmcBudgetCase):
    """AD-115: the two CMC groups plus the read-only responsible group, company-scoped."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "BUD Co B"})
        cls.user = new_test_user(cls.env, login="bud_sec_user", groups="base.group_user,cmc_base.group_cmc_user",
                                 company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.responsible = new_test_user(cls.env, login="bud_sec_resp", groups="base.group_user,cmc_base.group_cmc_responsible",
                                        company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.outsider = new_test_user(cls.env, login="bud_sec_out", groups="base.group_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.user_b = new_test_user(cls.env, login="bud_sec_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)])
        cls.mine = cls._center("BUD sec mine")
        cls.theirs = cls._center("BUD sec theirs")
        cls.env["cmc.responsibility.center"].create({"name": "BUD sec own", "owner_id": cls.responsible.id,
                                                     "center_ids": [Command.set(cls.mine.ids)]})
        cls.version = cls._version(name="BUD sec 2033")
        cls.line_mine = cls._line(cls.version, cls.mine, 3, 100.0)
        cls.line_theirs = cls._line(cls.version, cls.theirs, 3, 900.0)
        cls._cost(cls.mine, 120.0, d(3, 5))
        cls._cost(cls.theirs, 800.0, d(3, 5))
        cls.version_b = cls._version(name="BUD sec B", company=cls.company_b)
        cls.center_b = cls._center("BUD sec B center", company=cls.company_b)
        cls.line_b = cls._line(cls.version_b, cls.center_b, 3, 50.0)

    def _lines_seen(self, user):
        return self.Line.with_user(user).search([("id", "in", (self.line_mine | self.line_theirs | self.line_b).ids)])

    def test_user_edits_draft_budget(self):
        # AD-104: group_cmc_user creates and edits draft budgets
        version = self.Version.with_user(self.user).create({"name": "BUD sec by user", "date_from": d(1), "date_to": d(12, 31)})
        line = self.Line.with_user(self.user).create({"version_id": version.id, "center_id": self.mine.id,
                                                      "element_id": self.rent.id, "date": d(4), "figure": 10.0})
        line.figure = 20.0
        version.with_user(self.user)._cmc_budget_add({"center_id": self.mine.id, "element_id": self.rent.id}, 1200.0)
        self.assertEqual(len(version.budget_line_ids), 13)

    def test_user_cannot_approve(self):
        # AD-104: only a manager approves a budget
        with self.assertRaises(AccessError):
            self.version.with_user(self.user).action_approve()
        self.version.with_user(self.manager).action_approve()
        self.assertEqual(self.version.state, "done")

    def test_user_cannot_edit_approved_budget(self):
        # AD-104: approved = locked, for the user who drafted it too
        self.version.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            self.line_mine.with_user(self.user).figure = 1.0

    def test_user_computes_variance_run(self):
        # AD-115: group_cmc_user runs the engines
        self.version.with_user(self.manager).action_approve()
        run = self.env["cmc.budget.variance.run"].with_user(self.user).create(
            {"name": "BUD sec var", "version_id": self.version.id, "date_from": d(3), "date_to": d(3, 31)}
        )
        run.action_compute()
        self.assertEqual(run.line_ids.center_id, self.mine | self.theirs)

    def test_cmc_user_sees_all_centers(self):
        # AD-115: the user rule lifts the responsible filter, within the company
        self.assertEqual(self._lines_seen(self.user), self.line_mine | self.line_theirs)

    def test_responsible_sees_only_own_centers_budget(self):
        # BUD-05: the responsible reads the budget of his own centers
        self.assertEqual(self._lines_seen(self.responsible), self.line_mine)
        self.assertEqual(self.line_mine.with_user(self.responsible).figure, 100.0)
        self.assertTrue(self.line_mine.with_user(self.responsible).version_id.name)

    def test_responsible_sees_only_own_bva(self):
        # BUD-05: budget vs actual of his own centers only
        self.env.flush_all()
        rows = self.env["cmc.budget.bva"].with_user(self.responsible).search([("version_id", "=", self.version.id)])
        self.assertEqual(rows.mapped("center_id"), self.mine)
        self.assertAlmostEqual(sum(rows.mapped("variance")), -20.0)

    def test_responsible_is_read_only(self):
        # AD-115: read-only group, no budget edit and no engines
        with self.assertRaises(AccessError):
            self.line_mine.with_user(self.responsible).write({"figure": 1.0})
        with self.assertRaises(AccessError):
            self.Line.with_user(self.responsible).create({"version_id": self.version.id, "center_id": self.mine.id,
                                                          "date": d(5), "figure": 1.0})
        with self.assertRaises(AccessError):
            self.env["cmc.budget.variance.run"].with_user(self.responsible).create(
                {"name": "x", "version_id": self.version.id, "date_from": d(3), "date_to": d(3, 31)}
            )

    def test_outsider_has_no_access(self):
        # AD-115: no CMC group, no budget
        with self.assertRaises(AccessError):
            self.Line.with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.env["cmc.budget.bva"].with_user(self.outsider).search([])

    def test_other_company_isolated(self):
        # AD-115: company rule parent_of on budget lines and BvA
        self.assertEqual(self._lines_seen(self.user_b), self.line_b)
        self.env.flush_all()
        rows = self.env["cmc.budget.bva"].with_user(self.user).search([("version_id", "=", self.version_b.id)])
        self.assertFalse(rows)

    def test_center_of_other_company_refused(self):
        # AD-115: _check_company on the budget line
        with self.assertRaises(UserError):  # ValidationError subclasses UserError
            self._line(self.version, self.center_b, 4, 10.0)
