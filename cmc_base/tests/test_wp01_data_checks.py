"""WP-01 / INT-05 - data validation: driver volumes vs system totals, reasonableness vs prior period.

CONTRACT
- AbstractModel ``cmc.data.check`` (no table), ``api.model`` methods returning lists of warning dicts
  (no exception: they are warnings; engines keep failing loud on conservation, AD-113):
  - ``_cmc_check_driver_volumes(company, date_from, date_to, tolerance=0.05)`` -> one dict per driver whose
    typed volume differs from the system volume by more than ``tolerance`` (relative to the system volume):
    ``{"driver": cmc.cost.driver, "typed": float, "system": float, "difference": typed - system}``.
    typed = sum of ``cmc.center.service.quantity`` of that driver (company or shared); system = sum of ACTUAL
    ``cmc.measure.quantity`` facts (``version_id`` empty) of that driver, company, months in the span.
    Drivers without system facts in the span are skipped (nothing to compare).
  - ``_cmc_check_prior_period(company, date_from, date_to, threshold=0.5)`` -> one dict per CMC center (type
    set or period center) whose cost in the span changes by more than ``threshold`` (relative) vs the
    prior span of the same number of months immediately before: ``{"center", "current", "prior", "change"}``
    with change = (current - prior) / prior. A center with prior 0 and current != 0 is reported with
    ``change`` = False. Costs are read with ``_cmc_cost_groups`` (two grouped reads, not per line).
  - Callers must be group_cmc_user (AccessError otherwise); aggregation in sudo inside the company.

Hand-computed: driver services 60 + 60 = 120 vs facts Jan 70 + Feb 60 = 130 -> difference -10 (7.7%):
warning at 5%, none at 10%. Prior period: center X March 1000 vs Feb 400 -> +150% (warning); center Y March 500
vs Feb 450 -> +11% (no warning at 50%); center Z March 300, Feb 0 -> warning with change False.
"""

from datetime import date

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDataChecks(TransactionCase):
    """INT-05: control totals and reasonableness checks surface bad inputs before they are certified."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Check = cls.env["cmc.data.check"]
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.env["res.company"].create({"name": "WP01 Checks Co"})
        cls.env.user.company_ids = [(4, cls.company.id)]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.aux = cls._center("WP01 C aux", "auxiliary")
        cls.final_1 = cls._center("WP01 C F1", "final")
        cls.final_2 = cls._center("WP01 C F2", "final")
        cls.driver = cls.env["cmc.cost.driver"].create(
            {"name": "WP01 C hours", "code": "WP01C_H", "company_id": cls.company.id}
        )
        cls.untracked = cls.env["cmc.cost.driver"].create(
            {"name": "WP01 C m2", "code": "WP01C_M2", "company_id": cls.company.id}
        )
        Service = cls.env["cmc.center.service"].with_company(cls.company)
        for receiver in (cls.final_1, cls.final_2):
            Service.create({"company_id": cls.company.id, "provider_id": cls.aux.id, "receiver_id": receiver.id,
                            "driver_id": cls.driver.id, "quantity": 60})
            Service.create({"company_id": cls.company.id, "provider_id": cls.aux.id, "receiver_id": receiver.id,
                            "driver_id": cls.untracked.id, "quantity": 10})
        Fact = cls.env["cmc.measure.quantity"].with_company(cls.company)
        for month, qty in ((1, 70.0), (2, 60.0)):
            Fact.create({"driver_id": cls.driver.id, "date": date(2031, month, 1), "quantity": qty,
                         "company_id": cls.company.id})
        # outside the span: must not count
        Fact.create({"driver_id": cls.driver.id, "date": date(2031, 3, 1), "quantity": 500.0,
                     "company_id": cls.company.id})
        cls.center_x = cls._center("WP01 C X", "final")
        cls.center_y = cls._center("WP01 C Y", "final")
        cls.center_z = cls._center("WP01 C Z", "final")
        for center, feb, mar in ((cls.center_x, 400.0, 1000.0), (cls.center_y, 450.0, 500.0), (cls.center_z, 0.0, 300.0)):
            for day, amount in ((date(2031, 2, 10), feb), (date(2031, 3, 10), mar)):
                if amount:
                    cls._cost(center, amount, day)
        cls.cmc_user = new_test_user(cls.env, login="wp01_check_user", groups="base.group_user,cmc_base.group_cmc_user",
                                     company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.outsider = new_test_user(cls.env, login="wp01_check_out", groups="base.group_user",
                                     company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype,
                              "company_id": cls.company.id})

    @classmethod
    def _cost(cls, center, amount, day):
        return cls.AL.with_company(cls.company).create(
            {"name": "cost", "account_id": center.id, "amount": -amount, "date": day, "company_id": cls.company.id}
        )

    def test_driver_volume_mismatch_warns(self):
        # INT-05: driver volumes vs system totals
        warnings = self.Check._cmc_check_driver_volumes(self.company, date(2031, 1, 1), date(2031, 2, 28))
        mine = [w for w in warnings if w["driver"] == self.driver]
        self.assertEqual(len(mine), 1)
        self.assertAlmostEqual(mine[0]["typed"], 120.0)
        self.assertAlmostEqual(mine[0]["system"], 130.0)
        self.assertAlmostEqual(mine[0]["difference"], -10.0)

    def test_driver_without_system_facts_skipped(self):
        # INT-05: no system total, nothing to compare
        warnings = self.Check._cmc_check_driver_volumes(self.company, date(2031, 1, 1), date(2031, 2, 28))
        self.assertFalse([w for w in warnings if w["driver"] == self.untracked])

    def test_driver_volume_within_tolerance(self):
        # INT-05: 7.7% is inside a 10% tolerance
        warnings = self.Check._cmc_check_driver_volumes(self.company, date(2031, 1, 1), date(2031, 2, 28), tolerance=0.10)
        self.assertFalse([w for w in warnings if w["driver"] == self.driver])

    def test_prior_period_reasonableness(self):
        # INT-05: reasonableness vs prior period (March vs February)
        warnings = self.Check._cmc_check_prior_period(self.company, date(2031, 3, 1), date(2031, 3, 31))
        by_center = {w["center"]: w for w in warnings}
        self.assertIn(self.center_x, by_center)
        self.assertAlmostEqual(by_center[self.center_x]["current"], 1000.0)
        self.assertAlmostEqual(by_center[self.center_x]["prior"], 400.0)
        self.assertAlmostEqual(by_center[self.center_x]["change"], 1.5)
        self.assertNotIn(self.center_y, by_center)
        self.assertIn(self.center_z, by_center)
        self.assertIs(by_center[self.center_z]["change"], False)

    def test_checks_run_by_cmc_user(self):
        # AD-115: a Controlling User runs the checks of his company
        warnings = self.Check.with_user(self.cmc_user)._cmc_check_prior_period(
            self.company, date(2031, 3, 1), date(2031, 3, 31)
        )
        self.assertIn(self.center_x, [w["center"] for w in warnings])

    def test_checks_refused_to_outsider(self):
        # AD-115: no CMC group, no figures
        with self.assertRaises(AccessError):
            self.Check.with_user(self.outsider)._cmc_check_prior_period(self.company, date(2031, 3, 1), date(2031, 3, 31))

    def test_prior_period_query_count_bounded(self):
        # AD-114: grouped reads, the query count does not grow with the analytic lines
        def count():
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            self.Check._cmc_check_prior_period(self.company, date(2031, 3, 1), date(2031, 3, 31))
            return self.cr.sql_log_count - before

        small = count()
        for i in range(40):
            self._cost(self.center_y, 1.0, date(2031, 3, 1 + i % 28))
            self._cost(self.center_y, 1.0, date(2031, 2, 1 + i % 28))
        large = count()
        self.assertLessEqual(large, small + 2, f"query count grows with the lines: {small} -> {large}")
