"""AD-115 - security of cmc_standard_cost, exercised AS the role (never only as the superuser).

CONTRACT: common.py §6.
"""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import Y_FROM, Y_TO, StdCase


@tagged("post_install", "-at_install")
class TestStdSecurity(StdCase):
    def _user_version(self):
        version = self.Version.with_user(self.user).create(
            {"name": "STD by user", "version_type": "standard", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        self.env["cmc.standard.price"].with_user(self.user).create({"version_id": version.id, "product_id": self.a.id, "price": 4.0})
        return version

    def test_user_builds_draft_standard(self):
        # AD-115: a controlling user prefills the rate table and rolls up the cards of a draft standard
        version = self._user_version()
        version.action_cmc_std_prefill_rates(self.budget.with_user(self.user))
        version.action_cmc_std_rollup(self.f.with_user(self.user))
        self.assertTrue(version.card_ids.filtered(lambda card: card.product_id == self.f))
        version.card_ids.line_ids[:1].price = 1.0

    def test_user_cannot_approve(self):
        # AD-104: only a manager approves a standard version
        version = self._user_version()
        version.action_cmc_std_prefill_rates(self.budget.with_user(self.user))
        version.action_cmc_std_rollup(self.f.with_user(self.user))
        with self.assertRaises(AccessError):
            version.action_approve()
        version.with_user(self.manager).action_approve()
        self.assertEqual(version.state, "done")

    def test_user_computes_variance_run_results_read_only(self):
        # AD-115: a user computes the variances; the results are written by Compute only
        run = self._run(self._std_version(), user=self.user)
        self.assertTrue(run.line_ids)
        with self.assertRaises(UserError):  # AccessError subclasses UserError
            run.line_ids[:1].price_variance = 0.0
        with self.assertRaises(UserError):  # AccessError subclasses UserError
            run.absorption_ids[:1].unlink()
        with self.assertRaises(UserError):  # AccessError subclasses UserError
            self.env["cmc.standard.variance.line"].with_user(self.user).create({"run_id": run.id, "line_type": "material"})

    def test_outsider_has_no_access(self):
        # AD-115: no CMC group, no standard cost data
        version = self._std_version()
        with self.assertRaises(AccessError):
            self._card(version, self.f).with_user(self.outsider).read(["unit_cost"])
        with self.assertRaises(AccessError):
            self.env["cmc.standard.rate"].with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.env["cmc.standard.variance.run"].with_user(self.outsider).search([])

    def test_other_company_cards_invisible(self):
        # AD-115: a user of company B does not see the standard of company A
        self._std_version()
        company_b = self.env["res.company"].create({"name": "STD company B"})
        user_b = new_test_user(
            self.env, login="std_user_b", groups="base.group_user,cmc_base.group_cmc_user,mrp.group_mrp_user",
            company_id=company_b.id, company_ids=[Command.set(company_b.ids)],
        )
        for model in ("cmc.standard.card", "cmc.standard.card.line", "cmc.standard.rate", "cmc.standard.price"):
            self.assertFalse(self.env[model].with_user(user_b).search([]), model)

    def test_other_company_variance_runs_invisible(self):
        # AD-115: variance runs and results are company-scoped
        self._run(self._std_version())
        company_b = self.env["res.company"].create({"name": "STD company B runs"})
        user_b = new_test_user(
            self.env, login="std_user_b2", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=company_b.id, company_ids=[Command.set(company_b.ids)],
        )
        for model in ("cmc.standard.variance.run", "cmc.standard.variance.line", "cmc.standard.absorption.line"):
            self.assertFalse(self.env[model].with_user(user_b).search([]), model)
