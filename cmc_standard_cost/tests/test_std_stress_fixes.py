"""Stress test 2026-10 (corsia B) - FX-010 (DL-005) push of standard prices, FX-004 (DL-002) Prefill Rates.

CONTRACT (new or changed, binding for the developer)
====================================================
FX-010 / DL-005:
  - ``cmc.plan.version.action_cmc_std_push_prices()`` WITHOUT the context key ``cmc_std_push_confirmed`` pushes
    nothing and returns an ``ir.actions.act_window`` on ``cmc.std.push.wizard`` (``res_id`` = the preview wizard);
  - ``cmc.std.push.wizard`` (TransientModel): ``version_id``, ``line_ids`` (``product_id``, ``old_cost`` = current
    company ``standard_price``, ``new_cost`` = card ``unit_cost``), one line per product the push would change
    (cost method ``standard``); ``action_confirm()`` runs the push (manager only, AccessError otherwise);
  - with ``cmc_std_push_confirmed=True`` the push runs and records on the version ``std_push_date`` (Datetime) and
    ``std_push_line_ids`` (O2m: ``product_id``, ``old_cost``, ``new_cost``) - the snapshot of what was pushed;
  - after a push ``action_reset_to_draft()`` raises UserError ("costs already pushed to the valuation: create a
    new version"); a version never pushed still resets.
  NOTE for the developer: ``test_std_publish.py`` calls ``action_cmc_std_push_prices()`` expecting an immediate
  push: those calls need ``with_context(cmc_std_push_confirmed=True)``.
FX-004 / DL-002: ``action_cmc_std_prefill_rates`` with no budget figure for the span raises UserError saying where to
  enter them, and leaves the existing rate lines untouched; the Prefill button asks confirmation (``confirm``).
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .common import Y_FROM, Y_TO, StdCase

CONFIRMED = {"cmc_std_push_confirmed": True}


@tagged("post_install", "-at_install")
class TestStdPushStress(StdCase):
    """FX-010 / DL-005: the push is previewed, recorded, and not silently 'undone' by a reset."""

    def setUp(self):
        super().setUp()
        self.f.with_company(self.company).standard_price = 29.0
        self.version = self._std_version()

    def _price(self, product):
        return product.with_company(self.company).standard_price

    def test_fx010_push_opens_preview_without_pushing(self):
        # DL-005: before the push the manager sees what changes; nothing is written yet
        action = self.version.with_user(self.manager).action_cmc_std_push_prices()
        self.assertIsInstance(action, dict)
        self.assertEqual(action.get("res_model"), "cmc.std.push.wizard")
        self.assertAlmostEqual(self._price(self.f), 29.0)

    def test_fx010_preview_lists_products_old_and_new_cost(self):
        # FX-010 AC: the confirmation shows the N products with current and new cost
        action = self.version.with_user(self.manager).action_cmc_std_push_prices()
        wizard = self.env["cmc.std.push.wizard"].browse(action["res_id"])
        rows = {line.product_id: (line.old_cost, line.new_cost) for line in wizard.line_ids}
        self.assertEqual(set(rows), {self.f, self.g, self.k})
        self.assertEqual(rows[self.f], (29.0, 32.0))
        self.assertEqual(rows[self.g], (0.0, 38.0))

    def test_fx010_confirm_pushes(self):
        # FC-09: the confirmation of the preview performs the push
        action = self.version.with_user(self.manager).action_cmc_std_push_prices()
        self.env["cmc.std.push.wizard"].with_user(self.manager).browse(action["res_id"]).action_confirm()
        self.assertAlmostEqual(self._price(self.f), 32.0)

    def test_fx010_confirm_refused_to_user(self):
        # AD-115: the preview does not open the valuation to a non-manager
        action = self.version.with_user(self.manager).action_cmc_std_push_prices()
        with self.assertRaises(AccessError):
            self.env["cmc.std.push.wizard"].with_user(self.user).browse(action["res_id"]).action_confirm()
        self.assertAlmostEqual(self._price(self.f), 29.0)

    def test_fx010_push_recorded_on_version(self):
        # DL-005: the version keeps date and old/new cost of every pushed product
        self.version.with_user(self.manager).with_context(**CONFIRMED).action_cmc_std_push_prices()
        self.assertTrue(self.version.std_push_date)
        rows = {line.product_id: (line.old_cost, line.new_cost) for line in self.version.std_push_line_ids}
        self.assertEqual(rows[self.f], (29.0, 32.0))
        self.assertEqual(rows[self.g], (0.0, 38.0))

    def test_fx010_reset_after_push_blocked(self):
        # DL-005: after the push the reset is refused: the user must not believe the push was undone
        self.version.with_user(self.manager).with_context(**CONFIRMED).action_cmc_std_push_prices()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.version.state, "done")
        self.assertAlmostEqual(self._price(self.f), 32.0)

    def test_fx010_reset_without_push_allowed(self):
        # DL-005: the block is about the push only; an approved, never pushed version still reopens
        self.version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.version.state, "draft")


@tagged("post_install", "-at_install")
class TestStdPrefillRatesStress(StdCase):
    """FX-004 / DL-002: Prefill Rates never empties the table in silence."""

    def setUp(self):
        super().setUp()
        self.version = self._std_version(approve=False)
        self.empty_budget = self.Version.create(
            {"name": "STD empty budget", "version_type": "budget", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        self.empty_budget.with_user(self.manager).action_approve()

    def test_fx004_prefill_rates_without_figures_raises(self):
        # DL-002: no budget figure in the span -> a message, not an empty rate table
        rates = self.version.std_rate_ids
        self.assertTrue(rates, "fixture: the version has rate lines")
        with self.assertRaises(UserError):
            self.version.with_user(self.user).action_cmc_std_prefill_rates(self.empty_budget)
        self.assertEqual(self.version.std_rate_ids, rates)

    def test_fx004_prefill_wizard_without_figures_raises(self):
        # DL-002: same rule through the wizard the user clicks
        rates = self.version.std_rate_ids
        wizard = self.env["cmc.standard.prefill.wizard"].with_user(self.manager).create(
            {"version_id": self.version.id, "budget_version_id": self.empty_budget.id}
        )
        with self.assertRaises(UserError):
            wizard.action_prefill()
        self.assertEqual(self.version.std_rate_ids, rates)

    def test_fx004_prefill_button_asks_confirmation(self):
        # DL-002: replacing existing rate lines is confirmed by the user (regression guard)
        arch = self.env["cmc.standard.prefill.wizard"].get_view(view_type="form")["arch"]
        self.assertRegex(arch, r'<button[^>]*name="action_prefill"[^>]*confirm="[^"]+"')


@tagged("post_install", "-at_install")
class TestStdPushSnapshotReadOnly(StdCase):
    """RN-002 / FX-010 / DL-005: the push record is evidence, written only by the push itself.

    CONTRACT (binding for the developer): ``std_push_date`` and ``std_push_line_ids`` of ``cmc.plan.version`` are
    written ONLY by ``action_cmc_std_push_prices`` (confirmed). A direct ``write`` of ``std_push_date`` by anybody
    (user or manager: clearing it would re-enable the reset after a push; faking it would block a legitimate
    reset) raises UserError (AccessError is a UserError too); ``cmc.std.push.line`` cannot be written nor deleted,
    not even by a manager (snapshot read-only for everybody).
    """

    def setUp(self):
        super().setUp()
        self.f.with_company(self.company).standard_price = 29.0
        self.version = self._std_version()

    def _push(self):
        self.version.with_user(self.manager).with_context(**CONFIRMED).action_cmc_std_push_prices()
        self.assertTrue(self.version.std_push_date, "fixture: the version is pushed")

    def test_rn002_user_cannot_clear_push_date(self):
        # RN-002: a cmc user clearing the push date would let the version be reset after a real push
        self._push()
        with self.assertRaises(UserError):
            self.version.with_user(self.user).write({"std_push_date": False})
        self.assertTrue(self.version.std_push_date)

    def test_rn002_manager_cannot_clear_push_date(self):
        # RN-002: the snapshot is not editable by hand, not even by the manager
        self._push()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).write({"std_push_date": False})
        self.assertTrue(self.version.std_push_date)

    def test_rn002_user_cannot_fake_push_date(self):
        # RN-002: a fake push date on a never-pushed approved version would block its legitimate reset
        with self.assertRaises(UserError):
            self.version.with_user(self.user).write({"std_push_date": "2026-01-01 10:00:00"})
        self.assertFalse(self.version.std_push_date)

    def test_rn002_push_lines_not_writable_by_manager(self):
        # RN-002: the pushed old/new costs are the record of what reached the valuation
        self._push()
        line = self.version.std_push_line_ids[:1]
        self.assertTrue(line, "fixture: the push recorded lines")
        with self.assertRaises(UserError):
            line.with_user(self.manager).write({"new_cost": 1.0})

    def test_rn002_push_lines_not_deletable_by_manager(self):
        # RN-002: deleting the snapshot lines would erase the evidence of the push
        self._push()
        lines = self.version.std_push_line_ids
        with self.assertRaises(UserError):
            lines.with_user(self.manager).unlink()
        self.assertEqual(self.version.std_push_line_ids, lines)

    def test_rn002_push_lines_not_writable_through_version(self):
        # RN-002: the O2m commands on the version are the same door as a direct write
        self._push()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).write({"std_push_line_ids": [(5, 0, 0)]})
        self.assertTrue(self.version.std_push_line_ids)

    def test_rn002_reset_after_push_still_blocked(self):
        # RN-002 guard: the manager reset after a push stays refused (FX-010)
        self._push()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.version.state, "done")
