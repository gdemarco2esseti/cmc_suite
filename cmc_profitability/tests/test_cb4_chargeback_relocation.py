"""CB-4 (ronin WP-09 RN-003) - chargeback postings are relocation postings, read through the shared predicate.

CONTRACT (part pinned here; whole contract in cmc_base/tests/test_relocation.py)
- Every line posted by ``cmc.transfer.charge`` (``cmc_transfer_charge_id`` set) has ``cmc_is_relocation`` True
  and is selected by ``account.analytic.line._cmc_relocation_domain(include=True)``.
- Consumers read chargeback lines exactly like allocation lines:
  (a) responsibility summary: the provider relief (+cost) is NOT revenue; a chargeback of the whole provider cost
      gives the same figures as an equivalent ``cmc.allocation`` (same element, same receivers);
  (b) cmc_account: the posted lines are not marked manual (``cmc_classified_manually`` False) and are not
      auto-classified (a classification rule on the center leaves the posted directness ``indirect``);
  (c) ``cmc.cost.function`` history of the provider ignores chargeback lines (K / v from the primary cost);
  (d) run-state lock: user / sudo writes and unlinks -> UserError, the CMC classification gate included.
- RN-001 (ronin, CB-4 review) invariant "link => flag": a line with ``cmc_transfer_charge_id`` set ALWAYS has
  ``cmc_is_relocation`` True: DB CHECK constraint declared in cmc_profitability (raw SQL -> CheckViolation) and
  ``create`` / ``write`` carrying ``cmc_transfer_charge_id`` outside ``CMC_RUN_STATE`` -> UserError (admin, sudo).
- RN-002 (ronin, CB-4 review) the provider cost read (``_cmc_primary_cost``) excludes ONLY chargeback postings
  (``cmc_transfer_charge_id``), NOT allocation postings:
  (A) step-down: an allocation INTO the provider is cost the provider charges out (building 300 -> IT, then a
      full chargeback of IT moves 1300: IT +1300, Z -780, S -520 with the 30 : 20 = 60/40 driver);
  (B) a provider with OUTGOING allocation postings (``cmc_allocation_id`` set, provider = allocation source) in the
      chargeback span -> UserError at Compute (double relief), nothing written.

FIXTURE (ProfitCase company A + CB-4 rows, March 2027)
  IT provider (auxiliary): fixed 800 + variable 200, element WP09 salaries (controllable). Receivers Z 30 h, S 20 h.
  Chargeback full cost: IT +800 / +200, Z -600, S -400. Equivalent allocation: IT 1000 -> Z 30 : S 20.
  Responsibility "CB4 IT" over IT: controllable 0, non-controllable 0, revenue 0, result 0 (both runs).
  Responsibility "CB4 Z" over Z: controllable 340 + 600 = 940, revenue 0, result -940 (both runs).
  Cost function history (2028-01..05) on a CB4 maintenance center: Q 100..500, C 1000/1700/2000/2300/3000,
  each month fully charged out to Z -> high-low v 5, K 500 (net 0 would give v 0, K 0).
"""

import calendar
from datetime import date

from psycopg2.errors import CheckViolation

from odoo import Command
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import D_FROM, D_TO, ProfitCase

HISTORY = [(1, 100.0, 1000.0), (2, 200.0, 1700.0), (3, 300.0, 2000.0), (4, 400.0, 2300.0), (5, 500.0, 3000.0)]
FIGURES = ("controllable_cost", "non_controllable_cost", "revenue", "result")


@tagged("post_install", "-at_install")
class TestCb4ChargebackRelocation(ProfitCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.it = cls._center("CB4 IT", center_type="auxiliary")
        cls._cost(cls.it, 800.0)
        cls._cost(cls.it, 200.0, behavior="variable")
        cls.hours = cls._driver("CB4ITH", "duration")
        Resp = cls.env["cmc.responsibility.center"]
        cls.resp_it = Resp.create({"name": "CB4 IT", "owner_id": cls.env.user.id, "center_ids": [Command.set(cls.it.ids)]})
        cls.resp_z = Resp.create({"name": "CB4 Z", "owner_id": cls.env.user.id, "center_ids": [Command.set(cls.z.ids)]})

    def _charge(self, provider=None, date_from=D_FROM, date_to=D_TO, receivers=None):
        receivers = receivers or ((self.z, 30.0), (self.s, 20.0))
        run = self.env["cmc.transfer.charge"].create({
            "name": "CB4 IT chargeback", "company_id": self.company.id, "date_from": date_from, "date_to": date_to,
            "provider_id": (provider or self.it).id, "driver_id": self.hours.id, "criterion": "full", "element_id": self.sal.id,
            "line_ids": [Command.create({"receiver_id": center.id, "quantity": qty}) for center, qty in receivers],
        })
        run.action_compute()
        return run

    def _allocation(self):
        alloc = self.env["cmc.allocation"].create({
            "name": "CB4 IT allocation", "company_id": self.company.id, "source_account_id": self.it.id,
            "driver_id": self.hours.id, "amount": 1000.0, "date": D_TO, "element_id": self.sal.id,
            "line_ids": [Command.create({"target_account_id": self.z.id, "quantity": 30.0}),
                         Command.create({"target_account_id": self.s.id, "quantity": 20.0})],
        })
        alloc.action_compute()
        return alloc

    def _summary(self):
        figures = (self.resp_it | self.resp_z)._cmc_responsibility_summary(D_FROM, D_TO)
        return {resp: {key: figures[resp.id][key] for key in FIGURES} for resp in (self.resp_it, self.resp_z)}

    def _assert_figures(self, actual, expected):
        for resp, values in expected.items():
            for key, value in values.items():
                with self.subTest(responsibility=resp.name, figure=key):
                    self.assertAlmostEqual(actual[resp][key], value)

    # ---- the flag and the helper -----------------------------------------------------

    def test_chargeback_lines_are_relocation(self):
        # CB-4: the chargeback engine sets the shared flag, the shared helper selects its postings
        run = self._charge()
        Line = self.env["account.analytic.line"]
        self.assertEqual(set(run.posted_line_ids.mapped("cmc_is_relocation")), {True})
        scope = Line._cmc_relocation_domain(include=True) & Domain("account_id", "in", (self.it | self.z | self.s).ids)
        self.assertEqual(Line.search(scope), run.posted_line_ids)

    # ---- (a) responsibility summary --------------------------------------------------

    def test_responsibility_provider_relief_is_not_revenue(self):
        # CB-4 (a) / ronin RN-003: IT 1000 charged out -> the provider shows revenue 0, its cost nets to 0
        self._charge()
        figures = self._summary()
        self._assert_figures(figures, {
            self.resp_it: {"controllable_cost": 0.0, "non_controllable_cost": 0.0, "revenue": 0.0, "result": 0.0},
            self.resp_z: {"controllable_cost": 940.0, "non_controllable_cost": 0.0, "revenue": 0.0, "result": -940.0},
        })

    def test_responsibility_chargeback_equals_allocation(self):
        # CB-4 (a): a chargeback moving the full cost reads as the equivalent allocation, figure by figure
        run = self._charge()
        by_chargeback = self._summary()
        run.action_reset_to_draft()
        self._allocation()
        by_allocation = self._summary()
        self._assert_figures(by_chargeback, by_allocation)

    # ---- (b) cmc_account classification ---------------------------------------------

    def test_chargeback_lines_not_marked_manual_nor_classified(self):
        # CB-4 (b): run postings keep the classification of their run, the engine and the manual marking skip them
        Rule = self.env["cmc.classification.rule"]
        for center in (self.it, self.z, self.s):
            Rule.create({"name": "CB4 direct %s" % center.name, "company_id": self.company.id, "center_id": center.id,
                         "cmc_directness": "direct"})
        run = self._charge()
        posted = run.posted_line_ids.sorted("id")
        self.assertRecordValues(posted, [
            {"cmc_classified_manually": False, "cmc_directness": "indirect", "cmc_cost_element_id": self.sal.id}
            for _line in posted
        ])
        posted._cmc_auto_classify()
        self.assertRecordValues(posted, [
            {"cmc_classified_manually": False, "cmc_directness": "indirect", "cmc_cost_element_id": self.sal.id}
            for _line in posted
        ])

    def test_classification_gate_refuses_chargeback_lines(self):
        # CB-4 (b)+(d): the CMC controller's classification gate does not open a relocation line
        line = self._charge().posted_line_ids[:1]
        with self.assertRaises(UserError):
            line.with_user(self.cmc_user).write({"cmc_directness": "direct"})
        self.assertRecordValues(line, [{"cmc_directness": "indirect", "cmc_classified_manually": False}])

    # ---- (c) cost-function history ---------------------------------------------------

    def test_cost_function_ignores_chargeback_lines(self):
        # CB-4 (c) / RN-003: each month is fully charged out, the estimate still reads the primary cost
        center = self._center("CB4 maintenance", center_type="auxiliary")
        for month, qty, cost in HISTORY:
            self._cost(center, cost, day=date(2028, month, 10))
            self.env["cmc.measure.quantity"].create({"driver_id": self.hours.id, "date": date(2028, month, 1),
                                                     "center_id": center.id, "quantity": qty, "company_id": self.company.id})
        for month, _qty, _cost in HISTORY:
            last_day = calendar.monthrange(2028, month)[1]
            self._charge(provider=center, date_from=date(2028, month, 1), date_to=date(2028, month, last_day), receivers=((self.z, 1.0),))
        fn = self.env["cmc.cost.function"].create({
            "name": "CB4 maintenance", "company_id": self.company.id, "center_id": center.id, "element_id": self.sal.id,
            "driver_id": self.hours.id, "behavior": "semi_variable", "method": "high_low",
            "date_from": date(2028, 1, 1), "date_to": date(2028, 6, 30),
        })
        fn.action_estimate()
        self.assertAlmostEqual(fn.variable_rate, 5.0)
        self.assertAlmostEqual(fn.fixed_amount, 500.0)

    # ---- (d) run-state lock ----------------------------------------------------------

    def test_chargeback_lines_locked(self):
        # CB-4 (d): relocation lines change only via their run, whoever writes (no su bypass)
        line = self._charge().posted_line_ids[:1]
        amount = line.amount
        with self.assertRaises(UserError):
            line.write({"amount": 1.0})
        with self.assertRaises(UserError):
            line.sudo().write({"amount": 1.0})
        with self.assertRaises(UserError):
            line.write({"cmc_is_relocation": False})
        with self.assertRaises(UserError):
            line.sudo().unlink()
        self.assertTrue(line.exists())
        self.assertAlmostEqual(line.amount, amount)

    # ---- RN-001: link => flag ---------------------------------------------------------

    def test_rn001_db_check_chargeback_link_implies_flag(self):
        # RN-001: the DB refuses a chargeback-linked line without the relocation flag
        run = self._charge()
        line = run.posted_line_ids[:1]
        ordinary = self._cost(self.z, 5.0)
        self.env.flush_all()
        for query, params in (
            ("UPDATE account_analytic_line SET cmc_is_relocation = false WHERE id = %s", [line.id]),
            ("UPDATE account_analytic_line SET cmc_transfer_charge_id = %s WHERE id = %s", [run.id, ordinary.id]),
        ):
            with self.subTest(query=query), self.assertRaises(CheckViolation), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
                self.env.cr.execute(query, params)
        self.env.invalidate_all()
        self.assertTrue(line.cmc_is_relocation)
        self.assertFalse(ordinary.cmc_transfer_charge_id)

    def test_rn001_chargeback_link_refused_outside_run(self):
        # RN-001: only the chargeback engine posts chargeback-linked lines; an ordinary line cannot be linked (no su bypass)
        run = self._charge()
        posted = run.posted_line_ids
        Line = self.env["account.analytic.line"]
        vals = {"name": "fake", "account_id": self.z.id, "amount": -5.0, "date": D_TO, "cmc_transfer_charge_id": run.id}
        with self.assertRaises(UserError):
            Line.create(dict(vals))
        with self.assertRaises(UserError):
            Line.sudo().create(dict(vals))
        ordinary = self._cost(self.z, 5.0)
        with self.assertRaises(UserError):
            ordinary.write({"cmc_transfer_charge_id": run.id})
        with self.assertRaises(UserError):
            ordinary.sudo().write({"cmc_transfer_charge_id": run.id})
        self.assertEqual(Line.search([("cmc_transfer_charge_id", "=", run.id)]), posted)
        run.action_reset_to_draft()
        self.assertTrue(ordinary.exists())

    # ---- RN-002: only chargebacks are excluded from the provider cost ------------------

    def test_rn002_step_down_allocation_into_provider_is_charged_out(self):
        # RN-002 (A): building 300 allocated to IT is IT cost -> the full chargeback moves 1300 (60/40)
        building = self._center("CB4 building", center_type="auxiliary")
        self._cost(building, 300.0)
        alloc = self.env["cmc.allocation"].create({
            "name": "CB4 building to IT", "company_id": self.company.id, "source_account_id": building.id,
            "driver_id": self.hours.id, "amount": 300.0, "date": D_TO, "element_id": self.sal.id,
            "line_ids": [Command.create({"target_account_id": self.it.id, "quantity": 1.0})],
        })
        alloc.action_compute()
        run = self._charge()
        moved = {}
        for line in run.posted_line_ids:
            moved[line.account_id] = moved.get(line.account_id, 0.0) + line.amount
        self.assertEqual(set(moved), {self.it, self.z, self.s})
        self.assertAlmostEqual(moved[self.it], 1300.0)
        self.assertAlmostEqual(moved[self.z], -780.0)
        self.assertAlmostEqual(moved[self.s], -520.0)

    def test_rn002_provider_with_outgoing_allocation_refused(self):
        # RN-002 (B): IT already relieved by an allocation in the span -> charging it out again is a double relief
        self._allocation()
        run = self.env["cmc.transfer.charge"].create({
            "name": "CB4 IT double relief", "company_id": self.company.id, "date_from": D_FROM, "date_to": D_TO,
            "provider_id": self.it.id, "driver_id": self.hours.id, "criterion": "full", "element_id": self.sal.id,
            "line_ids": [Command.create({"receiver_id": self.z.id, "quantity": 30.0}),
                         Command.create({"receiver_id": self.s.id, "quantity": 20.0})],
        })
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertFalse(run.posted_line_ids)
        self.assertFalse(self.env["account.analytic.line"].search([("cmc_transfer_charge_id", "=", run.id)]))
