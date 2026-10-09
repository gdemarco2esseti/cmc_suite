"""CB-4 (ronin WP-09 RN-003, CONSOLIDA) - one shared "relocation posting" predicate on analytic lines.

A relocation posting is an analytic line that only MOVES cost between centers (the posting of an
allocation run, of a chargeback run, of any future engine that relieves one center and charges others,
summing to 0). It is cost-base content (``cmc_excluded`` False) but it is never revenue, never behavior
evidence and never classification input. Today every consumer tests ``cmc_allocation_id`` by hand and
cmc_profitability adds ``cmc_transfer_charge_id`` to the lock only, so chargeback lines are mis-read.

CONTRACT
- ``account.analytic.line.cmc_is_relocation``: stored Boolean (``store=True``, ``readonly=True``,
  ``copy=False``, indexed), True on every line posted by a cost-moving run engine, False on every other
  line. It is set by the posting engines ONLY (cmc_base: ``cmc.allocation``; cmc_profitability:
  ``cmc.transfer.charge``; a future engine extends the same flag, never a new ad-hoc test). A user never
  sets nor clears it: ``create`` / ``write`` carrying ``cmc_is_relocation`` outside the run sentinel
  (``cmc_run_state`` = ``CMC_RUN_STATE``) -> UserError (AccessError included), whoever the caller
  (admin, CMC manager, ``sudo()``: same "no su bypass" rule as the allocation lock).
- ``account.analytic.line._cmc_relocation_domain(include=True|False)`` (api.model) -> ``odoo.fields.Domain``:
  ``include=True`` selects the relocation lines, ``include=False`` the other ones. It is THE predicate:
  every consumer below composes it, none re-tests ``cmc_allocation_id`` / ``cmc_transfer_charge_id``.
- Consumers (each one reads relocation lines through the shared predicate):
  1. run-state lock (``_cmc_check_allocation_lock`` or its successor): relocation lines change only via
     their run (compute / reset under the sentinel) -> UserError otherwise;
  2. ``cmc.responsibility.center._cmc_center_figures``: relocation lines are cost (net), never revenue;
  3. ``cmc.cost.function._cmc_history``: relocation lines are excluded from the behavior evidence;
  4. cmc_account ``_cmc_auto_classify`` / ``_cmc_classification_gate`` / manual marking at create: skip
     relocation lines (they keep the classification posted by their run, ``cmc_classified_manually`` False);
  5. ``cmc.allocation._cmc_allocated_share``: the overhead base excludes relocation lines;
  6. cmc_profitability ``cmc.transfer.charge._cmc_primary_cost``: the provider cost read excludes relocation
     lines via the shared helper (RN-002 generalised: no chargeback reads any other run's relocation).
Chargeback-side regressions: cmc_profitability/tests/test_cb4_chargeback_relocation.py.

Fixture: source 1000 primary cost, allocation 1000 split 3:1 -> A -750, B -250, source +1000 (3 lines,
all relocation); the primary cost line is not.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tests.common import TransactionCase, new_test_user, tagged

DAY = date(2031, 3, 15)


@tagged("post_install", "-at_install")
class TestRelocationPredicate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.source = cls._center("CB4 R common")
        cls.target_a = cls._center("CB4 R target A")
        cls.target_b = cls._center("CB4 R target B")
        cls.centers = cls.source | cls.target_a | cls.target_b
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "CB4 R m2", "code": "CB4R_M2"})
        cls.element = cls.env["cmc.cost.element"].create({"name": "CB4 R rent", "code": "CB4R_RENT"})
        cls.primary = cls.AL.create({"name": "rent", "account_id": cls.source.id, "amount": -1000.0, "date": DAY,
                                     "cmc_cost_element_id": cls.element.id})
        cls.allocation = cls.env["cmc.allocation"].create({
            "name": "CB4 R rent split", "source_account_id": cls.source.id, "driver_id": cls.driver.id, "amount": 1000.0,
            "date": DAY, "element_id": cls.element.id,
            "line_ids": [(0, 0, {"target_account_id": cls.target_a.id, "quantity": 3}),
                         (0, 0, {"target_account_id": cls.target_b.id, "quantity": 1})],
        })
        cls.allocation.action_compute()
        cls.manager = new_test_user(cls.env, login="cb4_reloc_manager", groups="base.group_user,cmc_base.group_cmc_manager")

    @classmethod
    def _center(cls, name):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": cls.company.id})

    def _scope(self):
        return Domain("account_id", "in", self.centers.ids)

    # ---- the flag -------------------------------------------------------------------

    def test_flag_is_stored_system_boolean(self):
        # CB-4: one stored, read-only flag is the predicate (searchable/groupable, never a form input)
        field = self.AL._fields["cmc_is_relocation"]
        self.assertEqual(field.type, "boolean")
        self.assertTrue(field.store)
        self.assertTrue(field.readonly)
        self.assertFalse(field.copy)

    def test_allocation_lines_are_relocation(self):
        # CB-4: every line posted by an allocation run is a relocation; the primary cost is not
        posted = self.allocation.posted_line_ids
        self.assertEqual(len(posted), 3)
        self.assertEqual(set(posted.mapped("cmc_is_relocation")), {True})
        self.assertFalse(self.primary.cmc_is_relocation)
        self.assertEqual(self.AL.search(self._scope() & Domain("cmc_is_relocation", "=", True)), posted)

    def test_reset_then_recompute_keeps_flag(self):
        # CB-4: the flag belongs to the posting, re-posting sets it again
        self.allocation.action_reset_to_draft()
        self.assertFalse(self.AL.search(self._scope() & Domain("cmc_is_relocation", "=", True)))
        self.allocation.action_compute()
        self.assertEqual(set(self.allocation.posted_line_ids.mapped("cmc_is_relocation")), {True})

    # ---- never set by users ---------------------------------------------------------

    def test_manager_cannot_create_relocation_line(self):
        # CB-4: a hand-made line cannot pose as a relocation (it would vanish from revenue and history)
        Line = self.AL.with_user(self.manager)
        # positive control: the manager CAN post a normal cost line, so the refusal below is the flag guard
        Line.create({"name": "ok", "account_id": self.target_a.id, "amount": -5.0, "date": DAY})
        with self.assertRaises(UserError):
            Line.create({"name": "fake", "account_id": self.target_a.id, "amount": 5.0, "date": DAY, "cmc_is_relocation": True})

    def test_manager_cannot_flag_primary_line(self):
        # CB-4: a primary cost cannot be turned into a relocation by hand
        line = self.AL.with_user(self.manager).create({"name": "mine", "account_id": self.target_a.id, "amount": -5.0, "date": DAY})
        line.write({"name": "mine renamed"})  # positive control: the manager can write his own line
        with self.assertRaises(UserError):
            line.write({"cmc_is_relocation": True})
        self.assertFalse(line.cmc_is_relocation)

    def test_admin_and_sudo_cannot_set_flag(self):
        # CB-4: same "no su bypass" rule as the allocation lock: only the posting engine (sentinel) sets it
        with self.assertRaises(UserError):
            self.primary.write({"cmc_is_relocation": True})
        with self.assertRaises(UserError):
            self.primary.sudo().write({"cmc_is_relocation": True})
        with self.assertRaises(UserError):
            self.AL.sudo().create({"name": "fake", "account_id": self.source.id, "amount": 1.0, "date": DAY, "cmc_is_relocation": True})
        self.assertFalse(self.primary.cmc_is_relocation)

    def test_flag_cannot_be_cleared_on_posting(self):
        # CB-4: a relocation line changes only via its run, the flag included
        line = self.allocation.posted_line_ids[:1]
        with self.assertRaises(UserError):
            line.write({"cmc_is_relocation": False})
        with self.assertRaises(UserError):
            line.sudo().write({"cmc_is_relocation": False})
        self.assertTrue(line.cmc_is_relocation)

    # ---- the shared domain helper ---------------------------------------------------

    def test_relocation_domain_helper(self):
        # CB-4: one Domain helper, include=True -> relocation lines, include=False -> the others
        include = self.AL._cmc_relocation_domain(include=True)
        exclude = self.AL._cmc_relocation_domain(include=False)
        self.assertIsInstance(include, Domain)
        self.assertIsInstance(exclude, Domain)
        self.assertEqual(self.AL.search(self._scope() & include), self.allocation.posted_line_ids)
        self.assertEqual(self.AL.search(self._scope() & exclude), self.primary)

    def test_relocation_domain_composes_in_read_group(self):
        # CB-4: consumers compose the helper in grouped reads (sudo cost readers)
        Line = self.AL.sudo()
        moved = dict(Line._read_group(self._scope() & Line._cmc_relocation_domain(include=True), ["account_id"], ["amount:sum"]))
        self.assertEqual(moved, {self.source: 1000.0, self.target_a: -750.0, self.target_b: -250.0})
