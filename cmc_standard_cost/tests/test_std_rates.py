"""FC-08 - predetermined rate table of a standard version (budget / normal or practical capacity). CONTRACT: common.py §1, §3."""

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import TODAY, Y_FROM, Y_TO, StdCase


@tagged("post_install", "-at_install")
class TestStdRates(StdCase):
    def test_prefill_overhead_rate_practical_capacity(self):
        # FC-08: fixed rate = budgeted fixed overhead / practical capacity, variable rate = variable overhead / budget level
        version = self._std_version(approve=False, products=self.env["product.product"])
        self.assertRecordValues(self._rate_line(version, self.z, "overhead"), [{
            "driver_id": self.hours.id, "capacity_basis": "practical", "budget_fixed": 1200.0, "budget_variable": 600.0,
            "capacity_qty": 120.0, "budget_qty": 100.0, "fixed_rate": 10.0, "variable_rate": 6.0, "rate": 16.0,
        }])

    def test_prefill_overhead_rate_normal_capacity(self):
        # FC-08: the denominator can be the normal capacity (1200 / 96 = 12.5)
        version = self._std_version(approve=False, basis="normal", products=self.env["product.product"])
        line = self._rate_line(version, self.z, "overhead")
        self.assertEqual(line.capacity_basis, "normal")
        self.assertAlmostEqual(line.capacity_qty, 96.0)
        self.assertAlmostEqual(line.fixed_rate, 12.5)

    def test_prefill_labour_rate_from_mod_budget(self):
        # STD-01: MOD standard rate per center = MOD budget / budget hours; MP/MOD never enter the overhead pool
        version = self._std_version(approve=False, products=self.env["product.product"])
        self.assertRecordValues(self._rate_line(version, self.z, "labour"), [{"budget_variable": 2000.0, "budget_fixed": 0.0, "rate": 20.0}])
        self.assertAlmostEqual(self._rate_line(version, self.y, "labour").rate, 30.0)
        # Y has MOD figures only: no overhead rate line
        self.assertFalse(self._rate_line(version, self.y, "overhead"))

    def test_prefill_center_without_capacity_fails_loud(self):
        # FC-08: a budgeted center without capacity analysis gets no silent rate 0
        orphan = self._center("STD orphan")
        self._budget_line(orphan, self.rent, "fixed", 500)
        version = self.Version.create(
            {"name": "STD orphan", "version_type": "standard", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        with self.assertRaisesRegex(UserError, "STD orphan"):
            version.action_cmc_std_prefill_rates(self.budget)

    def test_manual_rate_kept(self):
        # FC-08: a manual rate (permanent change only, rate aging) is typed and not recomputed
        version = self._std_version(approve=False, products=self.env["product.product"])
        line = self._rate_line(version, self.z, "overhead")
        line.write({"capacity_basis": "manual", "fixed_rate": 11.0, "variable_rate": 5.0})
        line.budget_fixed = 9999.0
        self.assertRecordValues(line, [{"fixed_rate": 11.0, "variable_rate": 5.0, "rate": 16.0}])

    def test_rate_line_unique_per_center_and_kind(self):
        # FC-08: one predetermined rate per center and kind in a version
        version = self._std_version(approve=False, products=self.env["product.product"])
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.env["cmc.standard.rate"].create(
                {"version_id": version.id, "center_id": self.z.id, "rate_kind": "overhead", "driver_id": self.hours.id}
            )
            self.env.flush_all()

    def test_rate_line_refused_on_budget_version(self):
        # AD-104: the standard tables live on standard versions only
        with self.assertRaises(ValidationError):
            self.env["cmc.standard.rate"].create({"version_id": self.budget.id, "center_id": self.z.id, "rate_kind": "overhead"})

    def test_rate_table_locked_when_approved(self):
        # AD-104: an approved version is a locked set of planned figures
        version = self._std_version(products=self.env["product.product"])
        with self.assertRaises(UserError):
            self._rate_line(version, self.z, "overhead").fixed_rate = 99.0
        with self.assertRaises(UserError):
            version.action_cmc_std_prefill_rates(self.budget)

    def test_approval_publishes_center_rates(self):
        # AD-108 / FC-08: approved predetermined rates are readable by any consumer from the registry
        version = self._std_version(products=self.env["product.product"])
        Rate = self.env["cmc.rate"]
        self.assertAlmostEqual(Rate._cmc_lookup(self.z, self.hours, TODAY, origin=version, component="fixed").value, 10.0)
        self.assertAlmostEqual(Rate._cmc_lookup(self.z, self.hours, TODAY, origin=version, component="variable").value, 6.0)
        self.assertAlmostEqual(Rate._cmc_lookup(self.z, self.hours, TODAY, origin=version, component="total").value, 16.0)
        labour = Rate.search([
            ("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", version.id), ("holder_model", "=", "account.analytic.account"),
            ("holder_id", "=", self.y.id), ("cost_element_id", "=", self.mod.id), ("component", "=", "total"),
        ])
        self.assertEqual(len(labour), 12, "one rate per month of the version span")
        self.assertEqual(set(labour.mapped("value")), {30.0})

    def test_reapproval_replaces_published_rates(self):
        # AD-108: reset + approve again must not collide with the unique index nor duplicate rates
        version = self._std_version(products=self.env["product.product"])
        version.with_user(self.manager).action_reset_to_draft()
        self._rate_line(version, self.z, "overhead").write({"capacity_basis": "manual", "fixed_rate": 11.0})
        version.with_user(self.manager).action_approve()
        rates = self.env["cmc.rate"].search([
            ("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", version.id), ("holder_id", "=", self.z.id),
            ("holder_model", "=", "account.analytic.account"), ("component", "=", "fixed"), ("cost_element_id", "=", False),
        ])
        self.assertEqual(len(rates), 12)
        self.assertEqual(set(rates.mapped("value")), {11.0})
