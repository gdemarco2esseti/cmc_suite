"""AD-107 - one measure catalogue (= the driver catalogue) and one quantity fact table (wave A0).

CONTRACT
- ``cmc.cost.driver`` gains: ``measure_kind`` Selection required, ``allocation`` (default) /
  ``statistical``; ``measure_type`` Selection ``transaction`` / ``duration`` / ``intensity``;
  ``source`` Selection required, ``manual`` (default) / ``feeder``; ``feeder_key`` Char.
- Model ``cmc.measure.quantity`` (company-scoped rule, ``_check_company_auto``):
  ``driver_id`` M2o cmc.cost.driver required; ``date`` Date required, NORMALISED to the first day of its
  month on create/write; ``company_id`` M2o required (default env.company); ``version_id`` M2o
  cmc.plan.version (empty = actual; planned facts must fall inside the version span -> ValidationError;
  locked with the version, see test_plan_version); ``center_id`` M2o account.analytic.account;
  ``cost_object_id`` M2o cmc.cost.object; ``quantity`` Float; ``feeder_key`` Char (empty = manual).
  ``check_company=True`` on version_id/center_id/cost_object_id. ACL: group_cmc_user r/w/c/u;
  manager full; non-CMC users none.
- Feeder hook (style of ``_cmc_bridge_profiles``): ``cmc.measure.quantity._cmc_feeder_profiles()``
  (api.model) returns a list of dicts declared by bridges:
  ``{"key": str, "model": str, "domain": list|Domain, "date_field": str,
     "quantity": "__count" | <field name summed>, "driver": <xmlid str> | <cmc.cost.driver record>,
     "center_field": str|False, "object_field": str|False, "object_type": str|False}``;
  the source company is the source model's ``company_id``.
- ``cmc.measure.quantity._cmc_refresh_feeders(date_from, date_to, company=None, keys=None)`` (api.model):
  for each profile (filtered by ``keys``) and each month of the span, REPLACES the actual facts
  (version_id empty) of that feeder key, company and month with fresh aggregates read by
  ``_read_group`` (never line by line). Manual facts, planned facts, other feeders, other months and
  other companies are untouched. Idempotent. Runs in sudo (engine).
- Cron ``cmc_base.ir_cron_cmc_measure_feeders`` on model ``cmc.measure.quantity``.
"""

from datetime import date
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

FEED_KEY = "test_center_line_count"


@tagged("post_install", "-at_install")
class TestMeasureQuantity(TransactionCase):
    """AD-107: a driver is a measure; one fact table holds actual and planned quantities."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Fact = cls.env["cmc.measure.quantity"]
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "CMC Measure Co B"})
        cls.env.user.company_ids = [(4, cls.company_b.id)]
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center_a = cls.env["account.analytic.account"].create({"name": "Measure A", "plan_id": plan.id})
        cls.center_b = cls.env["account.analytic.account"].create({"name": "Measure B", "plan_id": plan.id})
        cls.center_other_co = cls.env["account.analytic.account"].create(
            {"name": "Measure other co", "plan_id": plan.id, "company_id": cls.company_b.id}
        )
        cls.driver = cls.env["cmc.cost.driver"].create(
            {"name": "Postings (test feeder)", "code": "TFEED", "measure_kind": "statistical",
             "measure_type": "transaction", "source": "feeder", "feeder_key": FEED_KEY, "company_id": False}
        )
        cls.manual_driver = cls.env["cmc.cost.driver"].create({"name": "Manual hours", "code": "TMAN"})
        cls.user = new_test_user(cls.env, login="cmc_meas_user", groups="cmc_base.group_cmc_user")
        cls.outsider = new_test_user(cls.env, login="cmc_meas_outsider", groups="base.group_user")
        # the profile under test: count analytic lines per center and month (a stand-in for any bridge)
        cls.profile = {
            "key": FEED_KEY,
            "model": "account.analytic.line",
            "domain": [("name", "=like", "feed%")],
            "date_field": "date",
            "quantity": "__count",
            "driver": cls.driver,
            "center_field": "account_id",
            "object_field": False,
            "object_type": False,
        }

    def _feed_line(self, center, day, company=None):
        vals = {"name": "feed", "account_id": center.id, "amount": -1.0, "date": day}
        if company:
            vals["company_id"] = company.id
        return self.AL.create(vals)

    def _refresh(self, date_from, date_to, company=None):
        Fact = type(self.Fact)
        with patch.object(Fact, "_cmc_feeder_profiles", lambda model: [dict(self.profile)]):
            self.Fact._cmc_refresh_feeders(date_from, date_to, company=company, keys=[FEED_KEY])

    def _facts(self, company=None):
        facts = self.Fact.search(
            [("feeder_key", "=", FEED_KEY), ("company_id", "=", (company or self.company).id)], order="date, center_id"
        )
        return [(f.center_id, f.date, f.quantity) for f in facts]

    # ---- catalogue ------------------------------------------------------------

    def test_driver_defaults_to_manual_allocation_driver(self):
        # AD-107 inv.(3): existing drivers stay allocation drivers fed by hand
        self.assertEqual(self.manual_driver.measure_kind, "allocation")
        self.assertEqual(self.manual_driver.source, "manual")

    def test_statistical_measure(self):
        # AD-107: a KPI measure lives in the same catalogue
        self.assertRecordValues(
            self.driver, [{"measure_kind": "statistical", "measure_type": "transaction", "source": "feeder"}]
        )

    # ---- fact table -------------------------------------------------------------

    def test_fact_date_is_month_bucket(self):
        # AD-105: facts are keyed by the first day of the month
        fact = self.Fact.create({"driver_id": self.manual_driver.id, "date": date(2026, 2, 17), "quantity": 5.0})
        self.assertEqual(fact.date, date(2026, 2, 1))
        fact.write({"date": date(2028, 2, 29)})
        self.assertEqual(fact.date, date(2028, 2, 1))

    def test_planned_fact_inside_version_span(self):
        # AD-107: planned quantities belong to a version and to its span
        version = self.env["cmc.plan.version"].create(
            {"name": "Plan Q1", "date_from": date(2026, 1, 1), "date_to": date(2026, 3, 31)}
        )
        self.Fact.create({"driver_id": self.manual_driver.id, "date": date(2026, 3, 31), "quantity": 1.0,
                          "version_id": version.id})
        with self.assertRaises(ValidationError):
            self.Fact.create({"driver_id": self.manual_driver.id, "date": date(2026, 4, 1), "quantity": 1.0,
                              "version_id": version.id})

    def test_fact_company_consistency(self):
        # AD-115: a fact of company A cannot point at a version of company B
        version_b = self.env["cmc.plan.version"].create(
            {"name": "Plan B", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "company_id": self.company_b.id}
        )
        with self.assertRaises(UserError):
            self.Fact.create({"driver_id": self.manual_driver.id, "date": date(2026, 1, 1), "quantity": 1.0,
                              "company_id": self.company.id, "version_id": version_b.id})

    def test_user_enters_manual_fact(self):
        # AD-107/AD-115: a controlling user types actual quantities
        fact = self.Fact.with_user(self.user).create(
            {"driver_id": self.manual_driver.id, "date": date(2026, 1, 1), "quantity": 7.0, "center_id": self.center_a.id}
        )
        fact.write({"quantity": 8.0})
        self.assertEqual(fact.quantity, 8.0)

    def test_outsider_cannot_read_facts(self):
        # AD-115
        self.Fact.create({"driver_id": self.manual_driver.id, "date": date(2026, 1, 1), "quantity": 1.0})
        with self.assertRaises(AccessError):
            self.Fact.with_user(self.outsider).search([])

    def test_user_does_not_see_other_company_facts(self):
        # AD-115: company record rule
        foreign = self.Fact.create(
            {"driver_id": self.manual_driver.id, "date": date(2026, 1, 1), "quantity": 1.0, "company_id": self.company_b.id}
        )
        self.assertNotIn(foreign, self.Fact.with_user(self.user).search([]))

    # ---- feeder engine ----------------------------------------------------------

    def test_feeder_aggregates_by_center_and_month(self):
        # AD-107: the feeder loads quantity = count per center x month
        self._feed_line(self.center_a, date(2026, 1, 5))
        self._feed_line(self.center_a, date(2026, 1, 31))
        self._feed_line(self.center_b, date(2026, 1, 20))
        self._feed_line(self.center_a, date(2026, 2, 1))
        self._refresh(date(2026, 1, 1), date(2026, 2, 28))
        self.assertEqual(
            self._facts(),
            [(self.center_a, date(2026, 1, 1), 2.0), (self.center_b, date(2026, 1, 1), 1.0),
             (self.center_a, date(2026, 2, 1), 1.0)],
        )
        fact = self.Fact.search([("feeder_key", "=", FEED_KEY)], limit=1)
        self.assertEqual(fact.driver_id, self.driver)
        self.assertFalse(fact.version_id)

    def test_feeder_refresh_is_idempotent(self):
        # AD-107: refresh replaces, never appends
        self._feed_line(self.center_a, date(2026, 1, 5))
        self._feed_line(self.center_a, date(2026, 1, 6))
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(self._facts(), [(self.center_a, date(2026, 1, 1), 2.0)])

    def test_feeder_refresh_follows_source_changes(self):
        # AD-107: a deleted source row disappears from the facts at the next refresh
        line = self._feed_line(self.center_a, date(2026, 1, 5))
        self._feed_line(self.center_a, date(2026, 1, 6))
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        line.unlink()
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(self._facts(), [(self.center_a, date(2026, 1, 1), 1.0)])
        # a month emptied entirely leaves no stale fact behind
        self.AL.search([("name", "=like", "feed%")]).unlink()
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(self._facts(), [])

    def test_feeder_keeps_manual_planned_and_other_months(self):
        # AD-107: only the feeder's own actual facts of the refreshed months are replaced
        version = self.env["cmc.plan.version"].create(
            {"name": "Plan feed", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        )
        manual = self.Fact.create({"driver_id": self.driver.id, "date": date(2026, 1, 1), "quantity": 99.0,
                                   "center_id": self.center_a.id})
        planned = self.Fact.create({"driver_id": self.driver.id, "date": date(2026, 1, 1), "quantity": 50.0,
                                    "center_id": self.center_a.id, "version_id": version.id})
        self._feed_line(self.center_a, date(2026, 3, 3))
        self._refresh(date(2026, 3, 1), date(2026, 3, 31))
        march = self.Fact.search([("feeder_key", "=", FEED_KEY), ("date", "=", date(2026, 3, 1))])
        self._feed_line(self.center_a, date(2026, 1, 3))
        self._refresh(date(2026, 1, 1), date(2026, 1, 31))
        self.assertTrue(manual.exists())
        self.assertTrue(planned.exists())
        self.assertTrue(march.exists(), "refreshing January must not touch March")
        self.assertEqual(manual.quantity, 99.0)

    def test_feeder_is_company_scoped(self):
        # AD-114 (8)/AD-115: one company per refresh, facts in the source company
        self._feed_line(self.center_a, date(2026, 1, 5))
        self._feed_line(self.center_other_co, date(2026, 1, 5), company=self.company_b)
        self._refresh(date(2026, 1, 1), date(2026, 1, 31), company=self.company_b)
        self._refresh(date(2026, 1, 1), date(2026, 1, 31), company=self.company)
        self.assertEqual(self._facts(), [(self.center_a, date(2026, 1, 1), 1.0)])
        self.assertEqual(self._facts(self.company_b), [(self.center_other_co, date(2026, 1, 1), 1.0)])

    def test_feeder_runs_for_a_controlling_user(self):
        # AD-107: the refresh is an engine (sudo on the source), a CMC user can trigger it
        self._feed_line(self.center_a, date(2026, 1, 5))
        Fact = type(self.Fact)
        with patch.object(Fact, "_cmc_feeder_profiles", lambda model: [dict(self.profile)]):
            self.Fact.with_user(self.user)._cmc_refresh_feeders(date(2026, 1, 1), date(2026, 1, 31), keys=[FEED_KEY])
        self.assertEqual(self._facts(), [(self.center_a, date(2026, 1, 1), 1.0)])

    def test_feeder_query_count_does_not_grow_with_source_rows(self):
        # AD-114 (1): aggregated at the fact grain, never browsed line by line
        for _i in range(3):
            self._feed_line(self.center_a, date(2026, 1, 10))
        for _i in range(60):
            self._feed_line(self.center_a, date(2026, 2, 10))

        def count(date_from, date_to):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            self._refresh(date_from, date_to)
            self.env.flush_all()
            return self.env.cr.sql_log_count - before

        small = count(date(2026, 1, 1), date(2026, 1, 31))
        large = count(date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(self._facts()[-1], (self.center_a, date(2026, 2, 1), 60.0))
        self.assertLessEqual(large, small + 3, "60 source rows must cost the same queries as 3")

    def test_feeder_cron_exists(self):
        # AD-114 (8): feeders run in a cron per company and month
        cron = self.env.ref("cmc_base.ir_cron_cmc_measure_feeders")
        self.assertEqual(cron.model_id.model, "cmc.measure.quantity")
