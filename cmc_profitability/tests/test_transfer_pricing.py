"""WP-09 ALC-07 - internal service pricing and chargeback between centers (transfer prices).

Sources: gap register ALC-07; Fazzini 12.7 (five criteria: market, marginal, full, cost-plus, negotiated);
Kaplan & Cooper Ch15 (ABC-level transfer prices); Cokins Ch6 (chargeback by consumption x unit rate);
AD-108 (service unit cost is a published rate), AD-110 (every analytic line is an actual), AD-113 (run).

DECISION (AD-110, pinned here): a chargeback MOVES COST, it does not create it. The run posts system analytic
lines (``cmc_transfer_charge_id``) that relieve the provider of its cost and charge it to the receivers at FULL
cost by service units (largest remainder, split by behavior fixed / variable), so they sum to 0 and the company
cost base is unchanged. The transfer price (criterion) only prices the service: price - cost is the provider's
``internal_margin``, a management figure on the run results, NEVER posted (it would be cost out of nothing for
every analytic reader, and it is eliminated at company level).

CONTRACT
- ``cmc.transfer.charge`` (``_inherit = ["cmc.run.mixin"]``, ``_check_company_auto``, company rule parent_of):
  inputs ``name``, ``company_id``, ``date_from``, ``date_to``, ``provider_id`` (analytic center, check_company),
  ``driver_id`` (service unit), ``criterion`` Selection ``marginal`` / ``full`` / ``cost_plus`` / ``market`` /
  ``negotiated`` / ``abc``, ``markup_percent``, ``market_price``, ``negotiated_price``, ``abc_activity_id``,
  ``element_id`` (cost element of the posted lines, optional), ``line_ids`` -> ``cmc.transfer.charge.line``
  (run.line mixin: ``receiver_id`` center check_company, ``quantity`` > 0 else ValidationError; receiver =
  provider -> ValidationError).
- Compute: provider cost of the span (cmc_base cost reader, fixed and variable), ``service_units`` = sum of the
  line quantities; ``unit_cost`` = cost / units, ``unit_variable_cost`` = variable cost / units;
  ``transfer_price``: marginal = unit_variable_cost, full = unit_cost, cost_plus = unit_cost x (1 + markup/100),
  market = market_price, negotiated = negotiated_price, abc = ``cmc.rate._cmc_lookup(abc_activity_id, driver_id,
  date_from)`` (records the usage of the producing ABC run). market / negotiated price <= 0, abc without rate,
  or 0 service units -> UserError. Header outputs (natural sign): ``provider_cost`` (negative),
  ``provider_variable_cost``, ``service_units``, ``unit_cost``, ``unit_variable_cost``, ``transfer_price``,
  ``internal_margin_total``. Results ``result_ids`` -> ``cmc.transfer.charge.result``: ``receiver_id``,
  ``quantity``, ``cost_amount`` (-units share of the provider cost), ``charge_amount`` (-quantity x price),
  ``internal_margin`` (= cost_amount - charge_amount).
- Postings ``posted_line_ids`` (account.analytic.line with ``cmc_transfer_charge_id``): per (center, behavior),
  provider +cost, receivers -share; written / deleted by hand -> UserError; reset deletes them.
- Rates (AD-108): holder provider center, unit ``driver_id``, month of date_from: ``total`` = unit_cost,
  ``variable`` = unit_variable_cost, origin the charge run; reset removes them.
FIXTURE (company "WP09 TP", March 2027): IT provider fixed 800 + variable 200; receivers Z 30 h, S 20 h ->
units 50, unit cost 20, variable 4; cost moved Z -600 (F -480 V -120), S -400 (F -320 V -80).
ABC run: IT 100% -> HELP (hours), 40 h -> rate 25.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

D_FROM = date(2027, 3, 1)
D_TO = date(2027, 3, 31)
D_MID = date(2027, 3, 15)


@tagged("post_install", "-at_install")
class TestTransferPricing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "WP09 TP"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.sal = cls.env["cmc.cost.element"].create({"name": "WP09 TP sal", "code": "WP09TPS", "company_id": cls.company.id})
        Account = cls.env["account.analytic.account"]
        cls.it = Account.create({"name": "WP09 IT", "plan_id": cls.plan.id, "cmc_center_type": "auxiliary", "company_id": cls.company.id})
        cls.z = Account.create({"name": "WP09 TP Z", "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": cls.company.id})
        cls.s = Account.create({"name": "WP09 TP S", "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": cls.company.id})
        cls.hours = cls.env["cmc.cost.driver"].create({"name": "WP09 IT hours", "code": "WP09ITH", "measure_type": "duration",
                                                       "company_id": cls.company.id})
        for center, cost, behavior in ((cls.it, 800, "fixed"), (cls.it, 200, "variable"), (cls.z, 5000, "fixed")):
            cls.env["account.analytic.line"].create({
                "name": "wp09 tp", "account_id": center.id, "amount": -cost, "company_id": cls.company.id,
                "cmc_cost_element_id": cls.sal.id, "cmc_behavior": behavior, "date": D_MID,
            })
        cls.cmc_user = new_test_user(cls.env, login="wp09_tp_user", groups="base.group_user,cmc_base.group_cmc_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.outsider = new_test_user(cls.env, login="wp09_tp_outsider", groups="base.group_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])

    def _charge(self, criterion="cost_plus", compute=True, env=None, **vals):
        run = (env or self.env)["cmc.transfer.charge"].create({
            "name": "WP09 IT chargeback", "company_id": self.company.id, "date_from": D_FROM, "date_to": D_TO,
            "provider_id": self.it.id, "driver_id": self.hours.id, "criterion": criterion, "markup_percent": 10.0,
            "line_ids": [Command.create({"receiver_id": self.z.id, "quantity": 30.0}),
                         Command.create({"receiver_id": self.s.id, "quantity": 20.0})],
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    def _abc_rate_run(self):
        Act = self.env["cmc.abc.activity"]
        process = Act.create({"name": "WP09 IT process", "code": "WP09ITP", "node_type": "process"})
        help_desk = Act.create({"name": "WP09 Help desk", "code": "WP09ITA", "node_type": "activity", "parent_id": process.id,
                                "driver_id": self.hours.id})
        partner = self.env["res.partner"].create({"name": "WP09 TP internal user"})
        obj = self.env["cmc.cost.object"]._cmc_ensure_objects(partner, company=self.company, object_type="customer")
        resource = self.env["cmc.abc.resource"].create({"name": "WP09 IT res", "code": "WP09ITR", "center_id": self.it.id})
        abc = self.env["cmc.abc.run"].create({
            "name": "WP09 IT ABC", "company_id": self.company.id, "date_from": D_FROM, "date_to": D_TO,
            "resource_line_ids": [Command.create({"resource_id": resource.id, "activity_id": help_desk.id, "percent": 100})],
            "line_ids": [Command.create({"activity_id": help_desk.id, "cost_object_id": obj.id, "driver_id": self.hours.id, "driver_qty": 40})],
        })
        abc.action_compute()
        return abc, help_desk

    # ---- unit service cost and criteria ---------------------------------------------

    def test_unit_service_cost(self):
        # ALC-07: unit price = center cost / service units (full and variable)
        run = self._charge("full")
        self.assertRecordValues(run, [{"provider_cost": -1000.0, "provider_variable_cost": -200.0, "service_units": 50.0,
                                       "unit_cost": 20.0, "unit_variable_cost": 4.0, "transfer_price": 20.0,
                                       "internal_margin_total": 0.0}])

    def test_criteria_prices(self):
        # Fazzini 12.7: market, marginal, full, cost-plus, negotiated
        expected = {"marginal": 4.0, "full": 20.0, "cost_plus": 22.0, "market": 25.0, "negotiated": 21.0}
        for criterion, price in expected.items():
            with self.subTest(criterion=criterion):
                run = self._charge(criterion, market_price=25.0, negotiated_price=21.0)
                self.assertAlmostEqual(run.transfer_price, price)
                run.action_reset_to_draft()

    def test_abc_level_transfer_price(self):
        # Kaplan Ch15: the price is the ABC activity rate published by a computed ABC run (AD-108)
        abc, help_desk = self._abc_rate_run()
        run = self._charge("abc", abc_activity_id=help_desk.id)
        self.assertAlmostEqual(run.transfer_price, 25.0)
        self.assertAlmostEqual(run.internal_margin_total, 250.0)
        with self.assertRaises(UserError):
            abc.action_reset_to_draft()

    def test_chargeback_results(self):
        # ALC-07 / Cokins Ch6: billed by volume x unit price; price - cost is internal margin
        run = self._charge("cost_plus")
        rows = {r.receiver_id: r for r in run.result_ids}
        self.assertRecordValues(rows[self.z] | rows[self.s], [
            {"quantity": 30.0, "cost_amount": -600.0, "charge_amount": -660.0, "internal_margin": 60.0},
            {"quantity": 20.0, "cost_amount": -400.0, "charge_amount": -440.0, "internal_margin": 40.0},
        ])
        self.assertAlmostEqual(run.internal_margin_total, 100.0)

    def test_marginal_price_gives_negative_internal_margin(self):
        # Fazzini 12.7: a marginal-cost price leaves the provider's fixed cost unrecovered
        run = self._charge("marginal")
        self.assertAlmostEqual(run.internal_margin_total, -800.0)

    # ---- AD-110 decision: only cost moves --------------------------------------------

    def test_postings_move_full_cost_and_conserve(self):
        # AD-110: posted lines relieve the provider and charge receivers at cost, by behavior; sum = 0
        run = self._charge("cost_plus")
        posted = {
            (account, behavior): amount
            for account, behavior, amount in self.env["account.analytic.line"]._read_group(
                [("cmc_transfer_charge_id", "=", run.id)], ["account_id", "cmc_behavior"], ["amount:sum"]
            )
        }
        self.assertEqual(posted, {
            (self.it, "fixed"): 800.0, (self.it, "variable"): 200.0,
            (self.z, "fixed"): -480.0, (self.z, "variable"): -120.0,
            (self.s, "fixed"): -320.0, (self.s, "variable"): -80.0,
        })
        self.assertEqual(run.posted_line_ids, self.env["account.analytic.line"].search([("cmc_transfer_charge_id", "=", run.id)]))

    def test_markup_never_posted(self):
        # AD-110: the company cost base is unchanged by a chargeback, whatever the price
        centers = self.it | self.z | self.s
        before = sum(centers._cmc_cost_groups(D_FROM, D_TO, company=self.company).values())
        self._charge("market", market_price=25.0)
        after = sum(centers._cmc_cost_groups(D_FROM, D_TO, company=self.company).values())
        self.assertAlmostEqual(after, before)
        self.assertAlmostEqual(self.it._cmc_cost_groups(D_FROM, D_TO, company=self.company).get(self.it.id, 0.0), 0.0)

    def test_posted_lines_locked_and_reset_deletes(self):
        # AD-113: system postings are written by Compute only and removed by reset
        run = self._charge("full")
        line = run.posted_line_ids[:1]
        with self.assertRaises(UserError):
            line.amount = 1.0
        with self.assertRaises(UserError):
            line.unlink()
        run.action_reset_to_draft()
        self.assertFalse(self.env["account.analytic.line"].search_count([("cmc_transfer_charge_id", "=", run.id)]))

    def test_service_unit_cost_published(self):
        # AD-108: the unit service cost is a rate of the provider center, origin the run
        run = self._charge("full")
        Rate = self.env["cmc.rate"]
        self.assertAlmostEqual(Rate._cmc_lookup(self.it, self.hours, D_MID, origin=run, company=self.company).value, 20.0)
        self.assertAlmostEqual(Rate._cmc_lookup(self.it, self.hours, D_MID, origin=run, component="variable", company=self.company).value, 4.0)
        run.action_reset_to_draft()
        self.assertFalse(Rate._cmc_lookup(self.it, self.hours, D_MID, origin=run, company=self.company))

    # ---- validation -------------------------------------------------------------

    def test_line_validation(self):
        # ALC-07: units must be positive and a center cannot charge itself
        with self.assertRaises(ValidationError):
            self._charge(compute=False, line_ids=[Command.create({"receiver_id": self.z.id, "quantity": 0.0})])
        with self.assertRaises(ValidationError):
            self._charge(compute=False, line_ids=[Command.create({"receiver_id": self.it.id, "quantity": 5.0})])

    def test_price_inputs_required(self):
        # Fazzini 12.7: a market or negotiated criterion without a price, or abc without a rate, fails loud
        for criterion in ("market", "negotiated"):
            with self.subTest(criterion=criterion):
                run = self._charge(criterion, compute=False)
                with self.assertRaises(UserError):
                    run.action_compute()
        orphan = self.env["cmc.abc.activity"].create({"name": "WP09 no rate", "code": "WP09NR", "node_type": "process"})
        run = self._charge("abc", compute=False, abc_activity_id=orphan.id)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_inputs_locked_when_computed(self):
        # AD-113: a computed chargeback keeps its inputs
        run = self._charge("full")
        with self.assertRaises(UserError):
            run.line_ids[0].quantity = 99.0
        with self.assertRaises(UserError):
            run.criterion = "market"

    # ---- security ---------------------------------------------------------------

    def test_cmc_user_computes_outsider_cannot_read(self):
        # AD-115: a controlling user runs the chargeback; a non-CMC user has no access
        run = self._charge("full", compute=False, env=self.env(user=self.cmc_user))
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertEqual(len(run.with_user(self.cmc_user).result_ids), 2)
        with self.assertRaises(AccessError):
            run.with_user(self.outsider).read(["transfer_price"])
