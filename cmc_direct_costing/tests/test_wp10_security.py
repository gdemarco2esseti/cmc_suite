"""WP-10 / AD-115 - decisions run as real CMC users, stay inside their company, results are engine rows.

CONTRACT (part of wp10_common)
- ACL: ``cmc.decision`` group_cmc_user read/write/create (no unlink), manager full; lines
  (``cmc.decision.line`` / ``.activity`` / ``.mix.line`` / ``.cash.flow``) user and manager full; results
  (``cmc.decision.mix.result`` / ``.cash.flow.result``) user read only, manager full; plain internal users
  no access at all.
- Global record rules ``['|', ('company_id', '=', False), ('company_id', 'parent_of', company_ids)]`` on the
  decision, every line model and every result model (outside noupdate).
- Rate lookups are scoped to the decision company: rates of another company are never read.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .wp10_common import Wp10Case


@tagged("post_install", "-at_install")
class TestWp10Security(Wp10Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP10 Co B"})
        cls.user_b = new_test_user(cls.env, login="cmc_wp10_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)])

    def _quote(self, user=None, **kw):
        return self._decision("special_order", user=user, quote_price=1000.0,
                              activity_ids=[self._act(self.c_machining, self.mh, 2)], **kw)

    def test_cmc_user_computes_a_quote(self):
        # DIF-04: the person who quotes is a CMC user, not the administrator
        quote = self._quote(user=self.user)
        quote.action_compute()
        self.assertRecordValues(quote, [{"state": "done", "activity_cost": 240.0, "verdict": "alternative"}])

    def test_cmc_user_computes_an_investment(self):
        # DIF-06: the result table is created by the engine for a user with read-only rights on it
        decision = self._decision("investment", user=self.user, discount_rate=10.0,
                                  cash_flow_ids=[(0, 0, {"year": 0, "amount": -100}), (0, 0, {"year": 1, "amount": 110})])
        decision.action_compute()
        self.assertEqual(len(decision.cash_flow_result_ids), 2)

    def test_user_cannot_write_results(self):
        # AD-001: a CMC user reads the mix results, never types them
        decision = self._decision("mix", user=self.user, resource_available=10.0, mix_line_ids=[
            (0, 0, {"name": "A", "price_unit": 10, "variable_unit": 4, "resource_per_unit": 1, "max_demand": 5}),
        ])
        decision.action_compute()
        self.assertEqual(decision.mix_result_ids.planned_qty, 5.0)
        with self.assertRaises(AccessError):
            self.env["cmc.decision.mix.result"].with_user(self.user).create({"decision_id": decision.id, "planned_qty": 1.0})

    def test_user_cannot_delete_manager_can(self):
        # AD-115: deleting a decision is a manager's call
        quote = self._quote(user=self.user)
        with self.assertRaises(AccessError):
            quote.unlink()
        quote.with_user(self.manager).unlink()
        self.assertFalse(quote.exists())

    def test_plain_user_has_no_access(self):
        # AD-115: decisions are CMC data
        with self.assertRaises(AccessError):
            self._quote(user=self.outsider)
        quote = self._quote()
        with self.assertRaises(AccessError):
            quote.with_user(self.outsider).read(["differential"])

    def test_other_company_cannot_see(self):
        # AD-115: a company B user neither finds nor reads company A decisions and their lines
        quote = self._quote(user=self.user)
        quote.action_compute()
        Decision = self.env["cmc.decision"].with_user(self.user_b)
        self.assertFalse(Decision.search([("id", "=", quote.id)]))
        self.assertFalse(self.env["cmc.decision.activity"].with_user(self.user_b).search([("decision_id", "=", quote.id)]))
        with self.assertRaises(AccessError):
            quote.with_user(self.user_b).read(["quote_cost"])

    def test_rates_of_another_company_are_not_read(self):
        # AD-108 / AD-115: a company B quote cannot price with company A rates
        quote = self._decision("special_order", quote_price=1000.0, company_id=self.company_b.id,
                               activity_ids=[self._act(self.c_machining, self.mh, 2)])
        with self.assertRaises(UserError):
            quote.action_compute()
