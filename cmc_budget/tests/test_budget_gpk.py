"""ALC-06 - GPK dual-rate closing of support centers (Kaplan Ch.3 App): fixed on planned usage, variable at
the standard rate x actual usage; the spending variance stays in the support center.

Hand-computed (March 2033, direct method): provider P (maintenance) budget fixed 6,000 + variable 4,000 on
maintenance hours; planned usage R1 300 h, R2 100 h (rate 4,000 / 400 = 10/h); actual services R1 250 h,
R2 150 h; actual cost of P 10,600.
  dual rate: R1 = 6,000 x 300/400 + 10 x 250 = 7,000; R2 = 1,500 + 1,500 = 3,000; P distributes 10,000 and
  keeps a residual of 600 (its spending variance).
  actual basis (unchanged): R1 = 10,600 x 250/400 = 6,625; R2 = 3,975; P residual 0.
Two pools (P2): maintenance 4,000 / 400 h = 10/h, machine 3,000 / 100 mh = 30/h; actual maintenance R1 250
R2 150, machine R1 80 R2 20 -> R1 2,500 + 2,400 = 4,900; R2 1,500 + 600 = 2,100; P2 cost 7,000, residual 0.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import CmcBudgetCase, d

MARCH = {"date_from": d(3), "date_to": d(3, 31)}


@tagged("post_install", "-at_install")
class TestGpkClosing(CmcBudgetCase):
    """ALC-06: support centers are charged on planned capacity for fixed and actual usage for variable cost."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maint = cls.env["cmc.cost.driver"].create({"name": "BUD maintenance hours", "code": "BUDMAINT"})
        cls.provider = cls._center("BUD gpk maintenance", "auxiliary")
        cls.r1 = cls._center("BUD gpk R1")
        cls.r2 = cls._center("BUD gpk R2")
        cls.version = cls._version(name="BUD gpk 2033")
        cls._line(cls.version, cls.provider, 3, 6000.0, element=cls.rent, driver_id=cls.maint.id)
        cls._line(cls.version, cls.provider, 3, 4000.0, element=cls.energy, behavior="variable", driver_id=cls.maint.id)
        cls._qty(cls.maint, cls.r1, 3, 300.0, version=cls.version)
        cls._qty(cls.maint, cls.r2, 3, 100.0, version=cls.version)
        cls._service(cls.provider, cls.r1, cls.maint, 250.0)
        cls._service(cls.provider, cls.r2, cls.maint, 150.0)
        cls._cost(cls.provider, 10600.0, d(3, 10))

    @classmethod
    def _service(cls, provider, receiver, driver, qty):
        return cls.env["cmc.center.service"].create(
            {"provider_id": provider.id, "receiver_id": receiver.id, "driver_id": driver.id, "quantity": qty}
        )

    def _close(self, basis="dual_rate", version=None):
        run = self.env["cmc.closing.run"].with_user(self.manager).create({
            "name": "BUD gpk", "method": "direct", **MARCH, "allocation_basis": basis,
            "gpk_version_id": (version or self.version).id if basis == "dual_rate" else False,
        })
        run.action_compute()
        return run.sudo()

    def _lines(self, run):
        return {line.center_id: line for line in run.line_ids}

    def test_dual_rate_charges_fixed_on_planned_and_variable_on_actual(self):
        # ALC-06: fixed x planned share + rate x actual hours
        self._approve(self.version)
        lines = self._lines(self._close())
        self.assertAlmostEqual(lines[self.r1].cost_received, 7000.0)
        self.assertAlmostEqual(lines[self.r2].cost_received, 3000.0)

    def test_spending_variance_stays_in_support_center(self):
        # STD-03 / ALC-06: what the charges do not cover is never pushed to the receivers
        self._approve(self.version)
        line = self._lines(self._close())[self.provider]
        self.assertRecordValues(line, [{"cost_initial": 10600.0, "cost_distributed": 10000.0, "cost_residual": 600.0}])

    def test_actual_basis_unchanged(self):
        # ALC-06: the default closing still allocates the actual cost on actual usage
        lines = self._lines(self._close(basis="actual"))
        self.assertAlmostEqual(lines[self.r1].cost_received, 6625.0)
        self.assertAlmostEqual(lines[self.r2].cost_received, 3975.0)
        self.assertAlmostEqual(lines[self.provider].cost_residual, 0.0)

    def test_several_pools_per_support_center(self):
        # ALC-06: one rate per pool (labour hours, machine hours) inside one center
        mach = self.env["cmc.cost.driver"].create({"name": "BUD machine pool", "code": "BUDMPOOL"})
        p2 = self._center("BUD gpk P2", "auxiliary")
        version = self._version(name="BUD gpk pools")
        self._line(version, p2, 3, 4000.0, element=self.energy, behavior="variable", driver_id=self.maint.id)
        self._line(version, p2, 3, 3000.0, element=self.labour, behavior="variable", driver_id=mach.id)
        for driver, r1_qty, r2_qty in ((self.maint, 300.0, 100.0), (mach, 50.0, 50.0)):
            self._qty(driver, self.r1, 3, r1_qty, version=version)
            self._qty(driver, self.r2, 3, r2_qty, version=version)
        self._service(p2, self.r1, self.maint, 250.0)
        self._service(p2, self.r2, self.maint, 150.0)
        self._service(p2, self.r1, mach, 80.0)
        self._service(p2, self.r2, mach, 20.0)
        self._cost(p2, 7000.0, d(3, 11))
        self._approve(version)
        run = self._close(version=version)
        received = {line.center_id: line.cost_received for line in run.line_ids}
        # P (no line in this version) keeps the actual basis: 6,625 / 3,975 on top of P2's charges
        self.assertAlmostEqual(received[self.r1], 4900.0 + 6625.0)
        self.assertAlmostEqual(received[self.r2], 2100.0 + 3975.0)
        self.assertAlmostEqual(self._lines(run)[p2].cost_residual, 0.0)

    def test_draft_version_refused(self):
        # AD-113: the planned figures must be approved before a closing reads them
        with self.assertRaises(UserError):
            self._close()

    def test_version_locked_while_closing_computed(self):
        # AD-002: the GPK version cannot be reset while a computed closing reads it
        self._approve(self.version)
        run = self._close()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_reset_to_draft()
        run.with_user(self.manager).action_reset_to_draft()
        self.version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.version.state, "draft")
