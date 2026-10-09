"""WP-01 / BUD-05 (AD-115) - the "CMC responsible" group: read-only, own responsibility centers only.

CONTRACT
- New group ``cmc_base.group_cmc_responsible`` (privilege CMC), implied BY ``group_cmc_user`` (so a user /
  manager has it too). Its record rules are NOT noupdate.
- ``cmc.responsibility.center``: responsible group ACL read only; rule for the group
  ``[('owner_id', '=', user.id)]``; group_cmc_user gets an unrestricted rule (``[(1, '=', 1)]``) so the
  union lifts the filter; global company rule ``company_id parent_of company_ids``.
- ``cmc.center.cost.report`` (hierarchical pivot, actuals): responsible group ACL read only; rule for the
  group ``[('center_id.cmc_responsibility_ids.owner_id', '=', user.id)]`` (rows of centers mapped to a
  responsibility center the user owns); group_cmc_user unrestricted.
- A responsible-only user has no access to runs/configuration (closing runs, allocations, cost functions):
  AccessError on create.
- ``_cmc_responsibility_summary`` called by a responsible user on a responsibility center he does not own:
  AccessError.
"""

from datetime import date

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestResponsibleSecurity(TransactionCase):
    """BUD-05: show each manager only their own targets and actuals."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Resp = cls.env["cmc.responsibility.center"]
        cls.Report = cls.env["cmc.center.cost.report"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.mine = cls._center("WP01 Sec mine")
        cls.theirs = cls._center("WP01 Sec theirs")
        for center, amount in ((cls.mine, 100.0), (cls.theirs, 900.0)):
            cls.AL.create({"name": "cost", "account_id": center.id, "amount": -amount, "date": date(2031, 3, 10)})
        cls.responsible = new_test_user(cls.env, login="wp01_sec_resp",
                                        groups="base.group_user,cmc_base.group_cmc_responsible")
        cls.other_responsible = new_test_user(cls.env, login="wp01_sec_resp2",
                                              groups="base.group_user,cmc_base.group_cmc_responsible")
        cls.cmc_user = new_test_user(cls.env, login="wp01_sec_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.resp_mine = cls.Resp.create({"name": "WP01 Sec own", "owner_id": cls.responsible.id,
                                         "center_ids": [(6, 0, cls.mine.ids)]})
        cls.resp_theirs = cls.Resp.create({"name": "WP01 Sec other", "owner_id": cls.other_responsible.id,
                                           "center_ids": [(6, 0, cls.theirs.ids)]})

    @classmethod
    def _center(cls, name):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final",
                              "company_id": cls.company.id})

    def _report_centers(self, user):
        rows = self.Report.with_user(user).search([("center_id", "in", (self.mine | self.theirs).ids)])
        return rows.mapped("center_id")

    def test_group_implied_by_cmc_user(self):
        # AD-115: the responsible group is implied by group_cmc_user
        responsible = self.env.ref("cmc_base.group_cmc_responsible")
        self.assertIn(responsible, self.env.ref("cmc_base.group_cmc_user").all_implied_ids)
        self.assertTrue(self.cmc_user.has_group("cmc_base.group_cmc_responsible"))

    def test_responsible_sees_only_own_responsibility_centers(self):
        # BUD-05: record rule by owner
        visible = self.Resp.with_user(self.responsible).search([("id", "in", (self.resp_mine | self.resp_theirs).ids)])
        self.assertEqual(visible, self.resp_mine)

    def test_responsible_is_read_only(self):
        # AD-115: the group is read-only
        with self.assertRaises(AccessError):
            self.resp_mine.with_user(self.responsible).write({"name": "renamed"})
        with self.assertRaises(AccessError):
            self.Resp.with_user(self.responsible).create({"name": "new", "owner_id": self.responsible.id})
        with self.assertRaises(AccessError):
            self.resp_mine.with_user(self.responsible).unlink()

    def test_responsible_sees_only_own_actuals(self):
        # BUD-05: actuals of the hierarchical pivot restricted to the owner's centers
        self.assertEqual(self._report_centers(self.responsible), self.mine)
        self.assertEqual(self._report_centers(self.other_responsible), self.theirs)

    def test_cmc_user_sees_everything(self):
        # AD-115: group_cmc_user's unrestricted rule lifts the responsible filter
        self.assertEqual(self._report_centers(self.cmc_user), self.mine | self.theirs)
        visible = self.Resp.with_user(self.cmc_user).search([("id", "in", (self.resp_mine | self.resp_theirs).ids)])
        self.assertEqual(visible, self.resp_mine | self.resp_theirs)

    def test_responsible_cannot_run_engines(self):
        # AD-115: read-only, no runs and no configuration
        span = {"date_from": date(2031, 1, 1), "date_to": date(2031, 12, 31)}
        with self.assertRaises(AccessError):
            self.env["cmc.closing.run"].with_user(self.responsible).create(span)
        with self.assertRaises(AccessError):
            self.env["cmc.cost.function"].with_user(self.responsible).check_access("create")

    def test_summary_of_foreign_center_refused(self):
        # BUD-05: the summary does not open someone else's figures
        with self.assertRaises(AccessError):
            self.resp_theirs.with_user(self.responsible)._cmc_responsibility_summary(date(2031, 1, 1), date(2031, 12, 31))

    def test_summary_of_own_center(self):
        # BUD-05: the owner reads his controllable cost (unclassified cost = non-controllable)
        summary = self.resp_mine.with_user(self.responsible)._cmc_responsibility_summary(date(2031, 1, 1), date(2031, 12, 31))
        self.assertAlmostEqual(summary[self.resp_mine.id]["non_controllable_cost"], 100.0)

    def test_responsibility_center_company_scoped(self):
        # AD-115: two companies, global parent_of rule
        company_b = self.env["res.company"].create({"name": "WP01 Sec Co B"})
        foreign = self.Resp.create({"name": "WP01 Sec B", "owner_id": self.cmc_user.id, "company_id": company_b.id})
        visible = self.Resp.with_user(self.cmc_user).search([])
        self.assertNotIn(foreign, visible)
