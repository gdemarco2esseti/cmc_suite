"""RN-004 / AD-002 - the dependency register only records COMPUTED origins, under a row lock.

CONTRACT
- ``consumer_run._cmc_record_usage(origins)`` (under the ``CMC_RUN_STATE`` sentinel) raises ``UserError``
  when any origin inheriting ``cmc.run.mixin`` is not in state ``done``: a consumer cannot certify
  results read from a draft run (it could be edited or reset right after).
- Before checking the state it locks the origin rows (``SELECT ... FOR UPDATE`` on the origin table), so a
  concurrent reset of the origin cannot slip between the check and the usage insert.
"""

from contextlib import contextmanager
from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


@tagged("post_install", "-at_install")
class TestRunUsage(TransactionCase):
    """RN-004: usages are recorded on computed origins only, with the origin rows locked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Usage center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.env["account.analytic.line"].create(
            {"name": "cost", "account_id": cls.center.id, "amount": -500, "date": date(2026, 6, 15)}
        )
        cls.manager = new_test_user(cls.env, login="cmc_usage_manager", groups="cmc_base.group_cmc_manager")
        cls.Usage = cls.env["cmc.run.usage"].sudo()

    def _closing(self, compute=True):
        run = self.env["cmc.closing.run"].create(
            {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "method": "direct"}
        )
        if compute:
            run.action_compute()
        return run

    def _version(self, approve=True):
        version = self.env["cmc.plan.version"].create(
            {"name": "Usage budget", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        )
        if approve:
            version.with_user(self.manager).action_approve()
        return version

    def _record(self, consumer, origins):
        return consumer.with_context(cmc_run_state=CMC_RUN_STATE)._cmc_record_usage(origins)

    def _usages(self, consumer):
        return self.Usage.search([("consumer_model", "=", consumer._name), ("consumer_id", "=", consumer.id)])

    @contextmanager
    def _capture_queries(self):
        # same hook as assertQueries, without its exact-match assertion
        queries = []
        yield from self._patchExecute(queries, flush=True)

    def test_draft_run_origin_refused(self):
        # RN-004 / AD-002: a draft run is no certified source, a consumer cannot record it
        origin = self._closing(compute=False)
        consumer = self._closing()
        with self.assertRaises(UserError):
            self._record(consumer, origin)
        self.assertFalse(self._usages(consumer))

    def test_draft_version_origin_refused(self):
        # RN-004 / AD-104: an unapproved plan version cannot be read by a computed run
        consumer = self._closing()
        with self.assertRaises(UserError):
            self._record(consumer, self._version(approve=False))
        self.assertFalse(self._usages(consumer))

    def test_mixed_origins_refused_whole(self):
        # RN-004: one draft origin among computed ones refuses the whole call, nothing half-recorded
        consumer = self._closing()
        origins = self._version() | self._version(approve=False)
        with self.assertRaises(UserError):
            self._record(consumer, origins)
        self.assertFalse(self._usages(consumer))

    def test_computed_origin_recorded(self):
        # RN-004 golden path: a computed origin is recorded once
        consumer = self._closing()
        version = self._version()
        self._record(consumer, version)
        self.assertRecordValues(
            self._usages(consumer), [{"origin_model": "cmc.plan.version", "origin_id": version.id}]
        )

    def test_origin_rows_locked_for_update(self):
        # RN-004: the origin rows are locked so a concurrent reset cannot pass between check and insert
        consumer = self._closing()
        version = self._version()
        with self._capture_queries() as queries:
            self._record(consumer, version)
        normalized = [" ".join(str(q).lower().split()) for q in queries]
        locks = [q for q in normalized if "for update" in q and "cmc_plan_version" in q]
        self.assertTrue(locks, "no SELECT ... FOR UPDATE on cmc_plan_version:\n%s" % "\n".join(normalized))
