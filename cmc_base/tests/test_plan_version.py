"""AD-104 - planning version in cmc_base, locked with cmc.run.mixin (wave A0).

CONTRACT
- Model ``cmc.plan.version`` (``_inherit = ["cmc.run.mixin"]``), company-scoped (global rule
  ``company_id parent_of company_ids``, not noupdate):
  ``name`` Char required; ``company_id`` M2o required (default env.company);
  ``date_from`` / ``date_to`` Date required (``date_to >= date_from``, else ValidationError);
  ``version_type`` Selection required, base keys ``budget`` (default), ``forecast``, ``standard``,
  ``simulation`` (owning modules extend with ``selection_add``);
  ``parent_id`` M2o ``cmc.plan.version`` (the version it was copied from; set by ``copy()``);
  ``assumption_ids`` O2m ``cmc.plan.version.assumption`` (``version_id``, ``name`` required, ``note``);
  ``currency_id`` M2o res.currency (default company currency); ``currency_rate`` Float
  (company-currency units per 1 version-currency unit, default 1.0, must be > 0 -> ValidationError).
  ``state``: mixin keys ``draft`` / ``done`` (done = "Approved").
- ``_cmc_input_fields`` include date_from, date_to, version_type, company_id, currency_id, currency_rate
  (``name`` stays editable on an approved version).
- ``action_approve()`` = compute of the run (no results): draft -> done. ``action_approve``,
  ``action_compute`` and ``action_reset_to_draft`` are reserved to ``cmc_base.group_cmc_manager``
  (``AccessError`` for a plain ``group_cmc_user``); ``group_cmc_user`` creates/edits drafts.
- ``copy()`` -> new DRAFT version, ``parent_id`` = original, duplicates assumptions, planned quantity
  facts (``cmc.measure.quantity`` with ``version_id``) and planned rates (``cmc.rate`` whose origin
  is the version) onto the new version (set-based). Allowed for group_cmc_user, also from an approved
  version.
- ``_cmc_to_company_currency(amount) -> float``: amount x currency_rate, rounded to the company currency.
- ``_cmc_months() -> list[date]``: ``cmc_utils.month_range(date_from, date_to)``.
- Generic AD-002 dependency register (also used by AD-108): model ``cmc.run.usage``
  (``consumer_model``/``consumer_id``, ``origin_model``/``origin_id``, ``company_id``) and the mixin method
  ``consumer_run._cmc_record_usage(origins)`` (only under the ``CMC_RUN_STATE`` sentinel, else UserError).
  ``cmc.run.mixin._cmc_check_no_dependents`` (base) refuses reset/unlink of an origin while a COMPUTED
  consumer recorded a usage of it; resetting the consumer releases its usages.
"""

from datetime import date

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


@tagged("post_install", "-at_install")
class TestPlanVersion(TransactionCase):
    """AD-104: one named, dated, approvable set of planned figures, locked by the run mixin."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Version = cls.env["cmc.plan.version"]
        cls.company = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "CMC Version Co B"})
        cls.user = new_test_user(cls.env, login="cmc_ver_user", groups="cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_ver_manager", groups="cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(cls.env, login="cmc_ver_outsider", groups="base.group_user")
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Machine hours (ver)", "code": "VERMH"})
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Version center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.env["account.analytic.line"].create(
            {"name": "cost", "account_id": cls.center.id, "amount": -500, "date": date(2026, 6, 15)}
        )

    def _version(self, env=None, **kw):
        vals = {"name": "Budget 2026", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        vals.update(kw)
        return (env or self.env)["cmc.plan.version"].create(vals)

    def _approved(self, **kw):
        version = self._version(**kw)
        version.with_user(self.manager).action_approve()
        return version

    def _computed_closing(self):
        run = self.env["cmc.closing.run"].create(
            {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "method": "direct"}
        )
        run.action_compute()
        return run

    # ---- shape -------------------------------------------------------------

    def test_version_defaults(self):
        # AD-104: a version is a draft budget in the company currency at rate 1 by default
        version = self._version()
        self.assertRecordValues(
            version,
            [{"state": "draft", "version_type": "budget", "company_id": self.company.id,
              "currency_id": self.company.currency_id.id, "currency_rate": 1.0, "parent_id": False}],
        )

    def test_base_version_types(self):
        # AD-104: base ships budget, forecast, standard, simulation
        keys = {key for key, _label in self.Version._fields["version_type"]._description_selection(self.env)}
        self.assertTrue({"budget", "forecast", "standard", "simulation"} <= keys)

    def test_span_must_be_ordered(self):
        # AD-104: date_to before date_from is refused
        with self.assertRaises(ValidationError):
            self._version(date_from=date(2026, 12, 1), date_to=date(2026, 1, 31))

    def test_months_of_the_span(self):
        # AD-105: a version spans calendar months
        version = self._version(date_from=date(2026, 11, 10), date_to=date(2027, 1, 5))
        self.assertEqual(version._cmc_months(), [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)])

    def test_assumption_register(self):
        # INT-04: free assumption notes per version
        version = self._version(assumption_ids=[(0, 0, {"name": "Inflation 2%", "note": "ISTAT forecast"})])
        self.assertEqual(version.assumption_ids.mapped("name"), ["Inflation 2%"])

    # ---- currency (INT-07) ----------------------------------------------------

    def test_fixed_rate_converts_to_company_currency(self):
        # INT-07: a version in a foreign currency converts with ONE fixed internal rate
        other = self.env["res.currency"].with_context(active_test=False).search(
            [("id", "!=", self.company.currency_id.id)], limit=1
        )
        other.active = True
        version = self._version(currency_id=other.id, currency_rate=0.9)
        self.assertAlmostEqual(version._cmc_to_company_currency(100.0), 90.0, places=2)

    def test_rate_must_be_positive(self):
        # INT-07: a zero or negative internal rate would wipe or flip every planned figure
        with self.assertRaises(ValidationError):
            self._version(currency_rate=0.0)
        with self.assertRaises(ValidationError):
            self._version(currency_rate=-1.0)

    # ---- approve = lock ----------------------------------------------------

    def test_manager_approves(self):
        # AD-104: approved version = done run
        version = self._approved()
        self.assertEqual(version.state, "done")

    def test_user_edits_draft_but_cannot_approve(self):
        # AD-104/AD-115: drafts are edited by group_cmc_user, approval is a manager decision
        version = self._version(env=self.env(user=self.user))
        version.with_user(self.user).write({"date_to": date(2026, 6, 30)})
        with self.assertRaises(AccessError):
            version.with_user(self.user).action_approve()
        # the generic compute of the mixin must not be a back door
        with self.assertRaises(AccessError):
            version.with_user(self.user).action_compute()
        self.assertEqual(version.state, "draft")

    def test_user_cannot_reset_approved_version(self):
        # AD-104: only the manager reopens an approved version
        version = self._approved()
        with self.assertRaises(AccessError):
            version.with_user(self.user).action_reset_to_draft()
        version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(version.state, "draft")

    def test_approved_header_locked(self):
        # AD-001 via mixin: the span, type and currency of an approved version are frozen
        version = self._approved()
        for vals in ({"date_to": date(2026, 6, 30)}, {"version_type": "forecast"}, {"currency_rate": 2.0}):
            with self.assertRaises(UserError):
                version.with_user(self.manager).write(vals)

    def test_approved_name_stays_editable(self):
        # AD-104: renaming is not changing the plan
        version = self._approved()
        version.with_user(self.manager).write({"name": "Budget 2026 (final)"})
        self.assertEqual(version.name, "Budget 2026 (final)")

    def test_planned_quantity_locked_when_approved(self):
        # AD-104 inv.(1): plan lines are run input lines, locked with the version
        version = self._version()
        fact = self.env["cmc.measure.quantity"].create(
            {"driver_id": self.driver.id, "date": date(2026, 3, 1), "quantity": 10.0, "version_id": version.id}
        )
        version.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            fact.write({"quantity": 11.0})
        with self.assertRaises(UserError):
            fact.unlink()
        with self.assertRaises(UserError):
            self.env["cmc.measure.quantity"].create(
                {"driver_id": self.driver.id, "date": date(2026, 4, 1), "quantity": 1.0, "version_id": version.id}
            )

    # ---- copy ------------------------------------------------------------------

    def test_copy_is_draft_child_with_lines(self):
        # AD-104: copy duplicates lines into a new draft version linked to its parent
        version = self._version(assumption_ids=[(0, 0, {"name": "Volume +5%"})])
        self.env["cmc.measure.quantity"].create(
            [{"driver_id": self.driver.id, "date": date(2026, m, 1), "quantity": 10.0 * m, "version_id": version.id}
             for m in (1, 2, 3)]
        )
        self.env["cmc.rate"]._cmc_publish(
            version, [{"holder": self.center, "driver": self.driver, "date": date(2026, 1, 1), "value": 42.0}]
        )
        version.with_user(self.manager).action_approve()

        child = version.with_user(self.user).copy({"version_type": "forecast"})

        self.assertRecordValues(child, [{"state": "draft", "parent_id": version.id, "version_type": "forecast"}])
        self.assertEqual(child.assumption_ids.mapped("name"), ["Volume +5%"])
        facts = self.env["cmc.measure.quantity"].search([("version_id", "=", child.id)], order="date")
        self.assertEqual(facts.mapped("quantity"), [10.0, 20.0, 30.0])
        rates = self.env["cmc.rate"].search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", child.id)])
        self.assertEqual(rates.mapped("value"), [42.0])
        # the original keeps its own lines untouched
        self.assertEqual(self.env["cmc.measure.quantity"].search_count([("version_id", "=", version.id)]), 3)
        # the copy is a draft: its lines are editable
        facts[0].write({"quantity": 99.0})

    # ---- AD-002 dependents -------------------------------------------------------

    def test_usage_recorded_only_by_engine(self):
        # AD-002: the dependency register is written by Compute, never by hand
        version = self._approved()
        closing = self._computed_closing()
        with self.assertRaises(UserError):
            closing._cmc_record_usage(version)

    def test_version_read_by_computed_run_cannot_be_reset(self):
        # AD-104 inv.(3): an approved version read by a computed run cannot be reopened
        version = self._approved()
        closing = self._computed_closing()
        closing.with_context(cmc_run_state=CMC_RUN_STATE)._cmc_record_usage(version)
        with self.assertRaises(UserError):
            version.with_user(self.manager).action_reset_to_draft()
        with self.assertRaises(UserError):
            version.unlink()
        # resetting the consumer releases the version
        closing.action_reset_to_draft()
        version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(version.state, "draft")

    # ---- security / multi-company -----------------------------------------------

    def test_outsider_has_no_access(self):
        # AD-115: no CMC group, no planning data
        self._version()
        with self.assertRaises(AccessError):
            self.env["cmc.plan.version"].with_user(self.outsider).search([])

    def test_user_does_not_see_other_company_versions(self):
        # AD-115: company record rule on the version
        own = self._version()
        foreign = self._version(company_id=self.company_b.id, name="Budget B")
        visible = self.env["cmc.plan.version"].with_user(self.user).search([])
        self.assertIn(own, visible)
        self.assertNotIn(foreign, visible)
