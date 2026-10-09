from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestCmcSecurity(TransactionCase):
    """The suite hooks account.analytic.line, a model the whole database writes
    to. Installing cmc_base must not make CMC rights a prerequisite for booking
    an analytic line, and CMC data must stay company-scoped."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.CA = cls.env["cmc.capacity.analysis"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center = cls.AA.create(
            {"name": "Security center", "plan_id": cls.plan.id,
             "cmc_center_type": "final"}
        )
        # a plain user who books analytic lines: timesheets, vendor bills, MRP.
        # No CMC group whatsoever.
        cls.outsider = new_test_user(
            cls.env, login="cmc_outsider",
            groups="base.group_user,analytic.group_analytic_accounting",
        )
        cls.cmc_user = new_test_user(
            cls.env, login="cmc_plain_user", groups="cmc_base.group_cmc_user",
        )

    def _vals(self, **kw):
        vals = {
            "name": "cost",
            "account_id": self.center.id,
            "amount": -100.0,
            "date": date(2026, 1, 15),
            "cmc_behavior": "fixed",
        }
        vals.update(kw)
        return vals

    def test_non_cmc_user_can_crud_analytic_lines(self):
        """A user without any CMC group must be able to book, edit and delete
        analytic lines: the capacity-sync hook runs on every one of them."""
        line = self.AL.with_user(self.outsider).create(self._vals())
        line.write({"name": "renamed"})
        line.write({"amount": -200.0})
        line.unlink()

    def test_non_cmc_user_line_still_updates_capacity(self):
        """The hook must keep doing its job for lines booked by outsiders —
        sudo is there to serve the data, not to skip it."""
        analysis = self.CA.create(
            {
                "center_id": self.center.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 31),
                "capacity_practical": 100.0,
                "capacity_actual": 80.0,
            }
        )
        self.assertEqual(analysis.fixed_cost, 0.0)
        self.AL.with_user(self.outsider).create(self._vals(amount=-500.0))
        self.assertEqual(analysis.fixed_cost, 500.0)
        self.assertEqual(analysis.imputed_fixed_cost, 400.0)
        self.assertEqual(analysis.unused_capacity_cost, 100.0)

    def test_capacity_sync_is_plan_aware(self):
        """A fixed cost booked on a non-default analytic plan must refresh the
        capacity row just like one on the default column."""
        alt_plan = self.env["account.analytic.plan"].create({"name": "Plant"})
        alt_center = self.AA.create(
            {"name": "Plant center", "plan_id": alt_plan.id,
             "cmc_center_type": "final"}
        )
        analysis = self.CA.create(
            {
                "center_id": alt_center.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 31),
                "capacity_practical": 50.0,
                "capacity_actual": 50.0,
            }
        )
        self.AL.create(
            self._vals(**{"account_id": False,
                          alt_plan._column_name(): alt_center.id,
                          "amount": -300.0})
        )
        self.assertEqual(analysis.fixed_cost, 300.0)

    def test_outsider_cannot_read_cmc_capacity(self):
        """Sudo in the hook must not widen what the user can actually read."""
        self.CA.create(
            {
                "center_id": self.center.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 31),
                "capacity_practical": 10.0,
            }
        )
        with self.assertRaises(AccessError):
            self.CA.with_user(self.outsider).search([])

    def test_cmc_user_reads_only_its_company(self):
        """Record rules keep capacity analyses company-scoped."""
        other_company = self.env["res.company"].create({"name": "CMC Co 2"})
        other_center = self.AA.create(
            {"name": "Co2 center", "plan_id": self.plan.id,
             "cmc_center_type": "final", "company_id": other_company.id}
        )
        foreign = self.CA.create(
            {
                "center_id": other_center.id,
                "company_id": other_company.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 31),
                "capacity_practical": 10.0,
            }
        )
        visible = self.CA.with_user(self.cmc_user).search([])
        self.assertNotIn(foreign, visible)

    def test_cost_element_of_another_company_is_refused(self):
        """CMC controllers write classification fields in sudo (AD-007), which
        skips record rules: the company check is what keeps elements apart."""
        other_company = self.env["res.company"].create({"name": "CMC Co 3"})
        foreign = self.env["cmc.cost.element"].create(
            {"name": "Foreign", "code": "FOREIGN", "company_id": other_company.id}
        )
        line = self.AL.create(
            {"name": "cost", "account_id": self.center.id, "amount": -10.0}
        )
        with self.assertRaises(UserError):
            line.write({"cmc_cost_element_id": foreign.id})
