"""WP-01 / ALC-01 + ALC-08 - the allocation becomes a run posting into the cost base; documented drivers.

CONTRACT
- ``cmc.allocation`` now ``_inherit = ["cmc.run.mixin"]`` (AD-113). New fields: ``date`` (Date, required,
  default today: posting date of the result), ``element_id`` (M2o cmc.cost.element, optional, check_company),
  ``posted_line_ids`` O2m ``account.analytic.line`` / ``cmc_allocation_id`` (= ``_cmc_result_fields``).
  ``_cmc_input_fields`` include ``source_account_id``, ``driver_id``, ``amount``, ``date``, ``element_id``,
  ``line_ids``; ``cmc.allocation.line`` is a ``cmc.run.line.mixin`` (``_cmc_run_field = "allocation_id"``).
  Existing behaviour of ``allocated_amount`` (largest remainder, conserving) is unchanged.
- ``action_compute()`` posts SYSTEM analytic lines (company of the allocation, plan-aware column of each
  center, ``cmc_allocation_id`` set, ``cmc_cost_element_id`` = ``element_id``, date = ``date``):
  one line per allocation line on the target center with ``amount = -allocated_amount`` (cost), and ONE
  counter line on ``source_account_id`` with ``amount = +amount`` (the source is emptied). Sum of posted
  amounts = 0 (conservation, fail loud otherwise). The lines are part of the cost base (``cmc_excluded``
  False), so ``_cmc_cost_groups`` and the closing read them.
- ``action_reset_to_draft()`` deletes the posted lines. Lines with ``cmc_allocation_id`` cannot be written
  or deleted outside the run sentinel (UserError). Inputs of a computed allocation are locked (UserError).
- Compute is allowed to group_cmc_user (as the closing); a user without CMC group gets AccessError.
- ``cmc.cost.driver`` (ALC-08): ``allocation_criterion`` Selection ``cause_effect`` / ``benefit`` / ``fairness``
  / ``ability_to_bear``; ``rationale`` Text; ``assignment`` Selection ``traced`` / ``allocated`` (required,
  default ``allocated``).
- ``cmc.allocation._cmc_allocated_share(company, date_from, date_to)`` (api.model) ->
  ``{"total_overhead", "allocated", "share", "warning"}``: total_overhead = cost of the company's cost-base
  lines in the span with ``cmc_directness = 'indirect'`` and WITHOUT ``cmc_allocation_id``; allocated = sum of
  ``amount`` of COMPUTED allocations dated in the span whose driver ``assignment = 'allocated'``;
  share = allocated / total_overhead * 100; warning = share > 10 (guardrail).

Hand-computed: source 1000 split 3:1 -> A 750, B 250, source counter +1000 -> source net 0.
Share: indirect 6000 + 4000 = 10000 (direct 5000 out); allocated driver 800 computed (+5000 draft, ignored),
traced driver 3000 -> share 8.0 %, no warning; + 400 allocated -> 12.0 %, warning.
"""

from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

DAY = date(2031, 3, 15)
SPAN = (date(2031, 3, 1), date(2031, 3, 31))


@tagged("post_install", "-at_install")
class TestAllocationRun(TransactionCase):
    """ALC-01: an allocation is booked into the cost base and the closing reads it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Alloc = cls.env["cmc.allocation"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.source = cls._center("WP01 A common", "final")
        cls.target_a = cls._center("WP01 A target A", "final")
        cls.target_b = cls._center("WP01 A target B", "final")
        cls.target_c = cls._center("WP01 A target C", "final")
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "WP01 A m2", "code": "WP01A_M2"})
        cls.element = cls.env["cmc.cost.element"].create({"name": "WP01 A rent", "code": "WP01A_RENT"})
        cls.AL.create({"name": "rent", "account_id": cls.source.id, "amount": -1000.0, "date": DAY})
        cls.cmc_user = new_test_user(cls.env, login="wp01_alloc_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.outsider = new_test_user(cls.env, login="wp01_alloc_out",
                                     groups="base.group_user,analytic.group_analytic_accounting")

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype,
                              "company_id": cls.company.id})

    def _allocation(self, amount=1000.0, weights=((3, "a"), (1, "b")), **kw):
        targets = {"a": self.target_a, "b": self.target_b, "c": self.target_c}
        vals = {"name": "WP01 rent split", "source_account_id": self.source.id, "driver_id": self.driver.id,
                "amount": amount, "date": DAY, "element_id": self.element.id,
                "line_ids": [(0, 0, {"target_account_id": targets[key].id, "quantity": qty}) for qty, key in weights]}
        vals.update(kw)
        return self.Alloc.create(vals)

    def _costs(self):
        return (self.source | self.target_a | self.target_b)._cmc_cost_groups(*SPAN, company=self.company)

    def test_draft_allocation_posts_nothing(self):
        # ALC-01: only a computed allocation reaches the cost base
        alloc = self._allocation()
        self.assertEqual(alloc.state, "draft")
        self.assertFalse(alloc.posted_line_ids)
        self.assertAlmostEqual(self._costs().get(self.target_a.id, 0.0), 0.0)

    def test_compute_posts_into_cost_base(self):
        # ALC-01: share_c = amount x param_c / sum(param), booked on the targets, source emptied
        alloc = self._allocation()
        alloc.action_compute()
        self.assertEqual(alloc.state, "done")
        self.assertEqual(len(alloc.posted_line_ids), 3)
        self.assertAlmostEqual(sum(alloc.posted_line_ids.mapped("amount")), 0.0)
        self.assertEqual(alloc.posted_line_ids.cmc_cost_element_id, self.element)
        self.assertEqual(set(alloc.posted_line_ids.mapped("date")), {DAY})
        costs = self._costs()
        self.assertAlmostEqual(costs[self.target_a.id], 750.0)
        self.assertAlmostEqual(costs[self.target_b.id], 250.0)
        self.assertAlmostEqual(costs.get(self.source.id, 0.0), 0.0)

    def test_posting_conserves_rounding(self):
        # ALC-01 / INT-05: 100 over three equal weights re-sums exactly
        alloc = self._allocation(amount=100.0, weights=((1, "a"), (1, "b"), (1, "c")))
        alloc.action_compute()
        targets = alloc.posted_line_ids.filtered(lambda line: line.amount < 0)
        self.assertEqual(sorted(-a for a in targets.mapped("amount")), [33.33, 33.33, 33.34])
        self.assertAlmostEqual(sum(alloc.posted_line_ids.mapped("amount")), 0.0)

    def test_closing_reads_posted_allocation(self):
        # ALC-01: closing/costing read the allocation output (it is in the cost base)
        alloc = self._allocation()
        alloc.action_compute()
        run = self.env["cmc.closing.run"].create({"date_from": SPAN[0], "date_to": SPAN[1], "method": "direct"})
        run.action_compute()
        line_a = run.line_ids.filtered(lambda line: line.center_id == self.target_a)
        self.assertAlmostEqual(line_a.cost_initial, 750.0)

    def test_reset_removes_posted_lines(self):
        # AD-113: reset deletes the results, the cost base goes back
        alloc = self._allocation()
        alloc.action_compute()
        posted = alloc.posted_line_ids
        alloc.action_reset_to_draft()
        self.assertFalse(posted.exists())
        self.assertAlmostEqual(self._costs()[self.source.id], 1000.0)

    def test_computed_inputs_locked(self):
        # AD-001: a computed allocation cannot change its inputs
        alloc = self._allocation()
        alloc.action_compute()
        with self.assertRaises(UserError):
            alloc.amount = 2000.0
        with self.assertRaises(UserError):
            alloc.line_ids[0].quantity = 9.0

    def test_posted_lines_locked(self):
        # ALC-01: system lines move only through compute/reset
        alloc = self._allocation()
        alloc.action_compute()
        line = alloc.posted_line_ids[0]
        with self.assertRaises(UserError):
            line.amount = -1.0
        with self.assertRaises(UserError):
            line.unlink()

    def test_cmc_user_computes_allocation(self):
        # AD-115: a Controlling User runs the allocation
        alloc = self._allocation()
        alloc.with_user(self.cmc_user).action_compute()
        self.assertEqual(alloc.state, "done")
        self.assertEqual(len(alloc.posted_line_ids), 3)

    def test_outsider_cannot_compute(self):
        # AD-115: no CMC group, no allocation
        alloc = self._allocation()
        with self.assertRaises(AccessError):
            alloc.with_user(self.outsider).action_compute()

    def test_posted_lines_in_allocation_company(self):
        # AD-115: lines are posted in the company of the allocation
        company_b = self.env["res.company"].create({"name": "WP01 A Co B"})
        self.env.user.company_ids = [(4, company_b.id)]
        src = self.AA.create({"name": "WP01 A src B", "plan_id": self.plan.id, "cmc_center_type": "final",
                              "company_id": company_b.id})
        tgt = self.AA.create({"name": "WP01 A tgt B", "plan_id": self.plan.id, "cmc_center_type": "final",
                              "company_id": company_b.id})
        driver_b = self.env["cmc.cost.driver"].create({"name": "WP01 A m2 B", "code": "WP01A_M2B",
                                                       "company_id": company_b.id})
        alloc = self.Alloc.with_company(company_b).create({
            "name": "B split", "company_id": company_b.id, "source_account_id": src.id, "driver_id": driver_b.id,
            "amount": 500.0, "date": DAY, "line_ids": [(0, 0, {"target_account_id": tgt.id, "quantity": 1})],
        })
        alloc.action_compute()
        self.assertEqual(alloc.posted_line_ids.company_id, company_b)


@tagged("post_install", "-at_install")
class TestAllocationDocumentation(TransactionCase):
    """ALC-08: every base is documented and the allocated share is reported against a 10% guardrail."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Alloc = cls.env["cmc.allocation"]
        cls.company_b = cls.env["res.company"].create({"name": "WP01 Share Co"})
        cls.env.user.company_ids = [(4, cls.company_b.id)]
        cls.company = cls.company_b  # a clean company: no demo overhead in the span
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        center = cls._center
        cls.source = center("WP01 S source")
        cls.other = center("WP01 S other")
        cls.target = center("WP01 S target")
        Driver = cls.env["cmc.cost.driver"]
        cls.allocated_driver = Driver.create({
            "name": "WP01 S headcount", "code": "WP01S_HC", "company_id": cls.company.id,
            "allocation_criterion": "fairness", "rationale": "No measurable cause", "assignment": "allocated",
        })
        cls.traced_driver = Driver.create({
            "name": "WP01 S kWh", "code": "WP01S_KWH", "company_id": cls.company.id,
            "allocation_criterion": "cause_effect", "rationale": "Metered", "assignment": "traced",
        })
        line = cls.AL.with_company(cls.company).create
        line({"name": "overhead", "account_id": cls.source.id, "amount": -6000.0, "date": DAY,
              "company_id": cls.company.id})
        line({"name": "overhead", "account_id": cls.other.id, "amount": -4000.0, "date": DAY,
              "company_id": cls.company.id})
        line({"name": "direct", "account_id": cls.other.id, "amount": -5000.0, "date": DAY,
              "company_id": cls.company.id, "cmc_directness": "direct"})

    @classmethod
    def _center(cls, name):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final",
                              "company_id": cls.company.id})

    def _allocation(self, driver, amount, compute=True, day=DAY):
        alloc = self.Alloc.with_company(self.company).create({
            "name": "WP01 S", "company_id": self.company.id, "source_account_id": self.source.id,
            "driver_id": driver.id, "amount": amount, "date": day,
            "line_ids": [(0, 0, {"target_account_id": self.target.id, "quantity": 1})],
        })
        if compute:
            alloc.action_compute()
        return alloc

    def test_driver_documentation_fields(self):
        # ALC-08: criterion, rationale, traced vs allocated
        Driver = self.env["cmc.cost.driver"]
        self.assertEqual(set(dict(Driver._fields["allocation_criterion"].selection)),
                         {"cause_effect", "benefit", "fairness", "ability_to_bear"})
        self.assertEqual(set(dict(Driver._fields["assignment"].selection)), {"traced", "allocated"})
        default = Driver.create({"name": "WP01 S default", "code": "WP01S_DEF"})
        self.assertEqual(default.assignment, "allocated")

    def test_allocated_share_below_guardrail(self):
        # ALC-08: % allocated = allocated cost / total overhead; traced and draft allocations excluded
        self._allocation(self.allocated_driver, 800.0)
        self._allocation(self.traced_driver, 3000.0)
        self._allocation(self.allocated_driver, 5000.0, compute=False)
        self._allocation(self.allocated_driver, 700.0, day=date(2031, 4, 15))
        share = self.Alloc._cmc_allocated_share(self.company, *SPAN)
        self.assertAlmostEqual(share["total_overhead"], 10000.0)
        self.assertAlmostEqual(share["allocated"], 800.0)
        self.assertAlmostEqual(share["share"], 8.0)
        self.assertFalse(share["warning"])

    def test_allocated_share_above_guardrail_warns(self):
        # ALC-08: above 10% the report raises the guardrail flag
        self._allocation(self.allocated_driver, 800.0)
        self._allocation(self.allocated_driver, 400.0)
        share = self.Alloc._cmc_allocated_share(self.company, *SPAN)
        self.assertAlmostEqual(share["share"], 12.0)
        self.assertTrue(share["warning"])
