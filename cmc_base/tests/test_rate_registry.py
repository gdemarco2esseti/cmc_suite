"""AD-108 - rate and unit-cost registry: producers publish, consumers read (wave A0).

CONTRACT
- Model ``cmc.rate`` (company-scoped rule): ``holder_model`` Char required + ``holder_id``
  Many2oneReference(model_field="holder_model") required (center / activity / resource / cost object);
  ``driver_id`` M2o cmc.cost.driver (the unit); ``cost_element_id`` M2o cmc.cost.element (optional);
  ``component`` Selection ``fixed`` / ``variable`` / ``total`` (default ``total``) required;
  ``date`` Date = first day of the month (normalised); ``value`` Float; ``company_id`` M2o required
  (= origin company); ``currency_id`` related company currency;
  ``origin_model`` Char required + ``origin_id`` Many2oneReference(model_field="origin_model"): a
  computed run (any ``cmc.run.mixin`` model) or a ``cmc.plan.version``.
  Unique index (holder_model, holder_id, driver, component, cost element, origin_model, origin_id, date)
  -> IntegrityError on a duplicate.
- ``cmc.rate._cmc_publish(origin, rows)`` (api.model) -> created ``cmc.rate`` records; each row is
  ``{"holder": record, "driver": cmc.cost.driver, "date": date, "value": float,
     "component": "total" (default), "cost_element": record|False}``.
  Origin = run: only under the ``CMC_RUN_STATE`` sentinel (result rows of the run, AD-001), else
  UserError; they are deleted by the run's ``action_reset_to_draft``.
  Origin = version: allowed while the version is draft (planned rates are inputs); UserError once it is
  approved; rates of an approved version cannot be written/unlinked/created (UserError).
- ``cmc.rate._cmc_lookup(holder, driver, on_date, origin=None, component="total", company=None)``
  (api.model) -> ``cmc.rate`` recordset of at most one row for the MONTH of ``on_date``:
  with ``origin`` -> that origin's rate; without -> the rate published by a computed run (never a
  version rate), the most recent origin first. Readable by group_cmc_user; consumers depend on cmc_base only.
- AD-002 over origins: a consumer run calls ``consumer._cmc_record_usage(rates.origin records)``;
  the producer run cannot be reset while that consumer is computed.
- ACL: group_cmc_user read/create/write (draft-version rates), manager full; non-CMC users none.
"""

from datetime import date

import psycopg2

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


@tagged("post_install", "-at_install")
class TestRateRegistry(TransactionCase):
    """AD-108: one registry of per-unit values, keyed by holder x unit x component x origin x month."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rate = cls.env["cmc.rate"]
        cls.company_b = cls.env["res.company"].create({"name": "CMC Rate Co B"})
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Rate center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.env["account.analytic.line"].create(
            {"name": "cost", "account_id": cls.center.id, "amount": -900, "date": date(2026, 1, 15)}
        )
        cls.hours = cls.env["cmc.cost.driver"].create({"name": "Machine hours (rate)", "code": "RATEMH"})
        cls.user = new_test_user(cls.env, login="cmc_rate_user", groups="cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_rate_manager", groups="cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(cls.env, login="cmc_rate_outsider", groups="base.group_user")

    def _closing(self):
        run = self.env["cmc.closing.run"].create(
            {"date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31), "method": "direct"}
        )
        run.action_compute()
        return run

    def _publish_run(self, run, value, day=date(2026, 1, 1), **row):
        return self.Rate.with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(
            run, [{"holder": self.center, "driver": self.hours, "date": day, "value": value, **row}]
        )

    def _version(self, **kw):
        vals = {"name": "Std 2026", "version_type": "standard", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        vals.update(kw)
        return self.env["cmc.plan.version"].create(vals)

    # ---- publish -------------------------------------------------------------------

    def test_run_publishes_rate(self):
        # AD-108: a computed run publishes its rates with itself as origin
        run = self._closing()
        rate = self._publish_run(run, 12.5, day=date(2026, 1, 20))
        self.assertRecordValues(
            rate,
            [{"holder_model": "account.analytic.account", "holder_id": self.center.id, "driver_id": self.hours.id,
              "component": "total", "date": date(2026, 1, 1), "value": 12.5, "origin_model": "cmc.closing.run",
              "origin_id": run.id, "company_id": run.company_id.id}],
        )

    def test_run_rates_need_the_sentinel(self):
        # AD-108/AD-001: run rates are result rows, written by Compute only
        run = self._closing()
        with self.assertRaises(UserError):
            self.Rate._cmc_publish(run, [{"holder": self.center, "driver": self.hours, "date": date(2026, 1, 1), "value": 1.0}])
        with self.assertRaises(UserError):
            self.Rate.with_user(self.user).create(
                {"holder_model": "account.analytic.account", "holder_id": self.center.id, "driver_id": self.hours.id,
                 "date": date(2026, 1, 1), "value": 1.0, "origin_model": "cmc.closing.run", "origin_id": run.id}
            )

    def test_reset_of_producer_deletes_its_rates(self):
        # AD-108: rates of a run are deleted by its reset (AD-001)
        run = self._closing()
        rate = self._publish_run(run, 12.5)
        run.action_reset_to_draft()
        self.assertFalse(rate.exists())

    def test_one_rate_per_key(self):
        # AD-108: unique index on (holder, unit, component, origin, month)
        run = self._closing()
        self._publish_run(run, 10.0)
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self._publish_run(run, 11.0, day=date(2026, 1, 25))
            self.env.flush_all()

    def test_components_are_distinct_keys(self):
        # AD-108: fixed and variable components of the same rate coexist
        run = self._closing()
        self._publish_run(run, 7.0, component="fixed")
        self._publish_run(run, 3.0, component="variable")
        fixed = self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 10), component="fixed")
        variable = self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 10), component="variable")
        self.assertEqual((fixed.value, variable.value), (7.0, 3.0))

    # ---- planned rates (version origin) --------------------------------------------

    def test_user_enters_planned_rate_on_draft_version(self):
        # AD-108: planned rates are inputs of a draft version
        version = self._version()
        rate = self.Rate.with_user(self.user)._cmc_publish(
            version, [{"holder": self.center, "driver": self.hours, "date": date(2026, 3, 1), "value": 15.0}]
        )
        self.assertEqual(rate.origin_model, "cmc.plan.version")
        rate.write({"value": 16.0})
        self.assertEqual(rate.value, 16.0)

    def test_planned_rates_locked_with_approved_version(self):
        # AD-108: rates of an approved version are locked with it
        version = self._version()
        rate = self.Rate._cmc_publish(version, [{"holder": self.center, "driver": self.hours, "date": date(2026, 3, 1), "value": 15.0}])
        version.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            rate.write({"value": 20.0})
        with self.assertRaises(UserError):
            rate.unlink()
        with self.assertRaises(UserError):
            self.Rate._cmc_publish(version, [{"holder": self.center, "driver": self.hours, "date": date(2026, 4, 1), "value": 1.0}])

    # ---- lookup --------------------------------------------------------------------------

    def test_lookup_by_month(self):
        # AD-105/AD-108: a January rate answers any day of January and nothing else
        run = self._closing()
        rate = self._publish_run(run, 12.5)
        self.assertEqual(self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 31)), rate)
        self.assertEqual(self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 1)), rate)
        self.assertFalse(self.Rate._cmc_lookup(self.center, self.hours, date(2026, 2, 1)))
        self.assertFalse(self.Rate._cmc_lookup(self.center, self.hours, date(2025, 12, 31)))

    def test_lookup_by_version_vs_actual(self):
        # AD-108: "standard vs actual" = planned rate vs computed rate on the same key
        run = self._closing()
        actual = self._publish_run(run, 12.5)
        version = self._version()
        planned = self.Rate._cmc_publish(version, [{"holder": self.center, "driver": self.hours, "date": date(2026, 1, 1), "value": 11.0}])
        self.assertEqual(self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 15)), actual)
        self.assertEqual(self.Rate._cmc_lookup(self.center, self.hours, date(2026, 1, 15), origin=version), planned)

    def test_lookup_without_rate_is_empty(self):
        # AD-108: a missing rate is an empty recordset, the consumer decides how to fail
        other = self.env["cmc.cost.driver"].create({"name": "Unused unit", "code": "RATEUNUSED"})
        result = self.Rate._cmc_lookup(self.center, other, date(2026, 1, 15))
        self.assertEqual(result._name, "cmc.rate")
        self.assertFalse(result)

    # ---- AD-002 over origins -----------------------------------------------------------

    def test_producer_read_by_computed_consumer_cannot_reset(self):
        # AD-108: a consumer records the origins it read; the producer cannot reopen under it
        producer = self._closing()
        rate = self._publish_run(producer, 12.5)
        consumer = self._closing()
        consumer.with_context(cmc_run_state=CMC_RUN_STATE)._cmc_record_usage(
            self.env[rate.origin_model].browse(rate.origin_id)
        )
        with self.assertRaises(UserError):
            producer.action_reset_to_draft()
        self.assertTrue(rate.exists())
        consumer.action_reset_to_draft()
        producer.action_reset_to_draft()
        self.assertFalse(rate.exists())

    # ---- security ----------------------------------------------------------------------

    def test_user_reads_rates(self):
        # AD-108: consumers read rates as controlling users
        run = self._closing()
        rate = self._publish_run(run, 12.5)
        found = self.Rate.with_user(self.user)._cmc_lookup(self.center, self.hours, date(2026, 1, 15))
        self.assertEqual(found.id, rate.id)

    def test_outsider_cannot_read_rates(self):
        # AD-115
        with self.assertRaises(AccessError):
            self.Rate.with_user(self.outsider).search([])

    def test_user_does_not_see_other_company_rates(self):
        # AD-115: company rule on the registry
        version_b = self._version(company_id=self.company_b.id, name="Std B")
        foreign = self.Rate._cmc_publish(version_b, [{"holder": self.center, "driver": self.hours, "date": date(2026, 1, 1), "value": 99.0}])
        self.assertNotIn(foreign, self.Rate.with_user(self.user).search([]))
        self.assertEqual(foreign.company_id, self.company_b)
