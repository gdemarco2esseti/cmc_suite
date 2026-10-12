"""WP-05 / AD-108 + AD-002 - rates read from and published to the ``cmc.rate`` registry.

CONTRACT (part)
- ``labour_rate_source = 'registry'``: hours x ``cmc.rate._cmc_lookup(center, labour_driver_id, month)``
  (run-published rates; category ignored, labour rows ``rate_origin = 'registry'``, ``category_id`` False).
- ``overhead_source = 'registry'``: hours x ``_cmc_lookup(center, overhead_driver_id, month)``; requires
  ``overhead_base = 'hours'`` and ``overhead_driver_id`` (ValidationError); ``registry`` labour requires
  ``labour_driver_id`` (ValidationError); a missing registry rate -> UserError naming the center.
- The job run records ``cmc.run.usage`` of the origins of the rates it read: the producer cannot be reset
  while the job run is computed; the job run reset releases it.
- ``table`` labour with a ``labour_driver_id``: Compute publishes the center-level table rates it applied
  (holder = center, driver, month, component total, origin = the job run); reset removes them.
Producer used here: a computed ``cmc.closing.run`` publishing through ``cmc.rate._cmc_publish`` (the real
publishing API). TODO: sostituire con il produttore FC-08 (WP-03) delle tariffe orarie di centro quando esiste.
Numbers: labour Z 45 / Y 65 -> J1 30 x 45 + 5 x 65 = 1675; overhead Z 12 / Y 20 -> J1 460, J2 96.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

from .common import D_FROM, D_TO, JobCase


@tagged("post_install", "-at_install")
class TestJobRegistry(JobCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        su = cls.su
        cls.hours_drv = su["cmc.cost.driver"].create({"name": "WP05 hours", "code": "WP05H", "company_id": cls.company_a.id})
        cls.oh_drv = su["cmc.cost.driver"].create({"name": "WP05 OH hours", "code": "WP05OH", "company_id": cls.company_a.id})
        cls.producer = su["cmc.closing.run"].create(
            {"name": "WP05 rate producer", "method": "direct", "company_id": cls.company_a.id,
             "date_from": D_FROM, "date_to": D_TO}
        )
        cls.producer.action_compute()
        su["cmc.rate"].with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(cls.producer, [
            {"holder": cls.z, "driver": cls.hours_drv, "date": D_FROM, "value": 45.0},
            {"holder": cls.y, "driver": cls.hours_drv, "date": D_FROM, "value": 65.0},
            {"holder": cls.z, "driver": cls.oh_drv, "date": D_FROM, "value": 12.0},
            {"holder": cls.y, "driver": cls.oh_drv, "date": D_FROM, "value": 20.0},
        ])

    def _published(self, run):
        return self.env["cmc.rate"].search([("origin_model", "=", "cmc.job.cost.run"), ("origin_id", "=", run.id)])

    def test_labour_from_registry(self):
        # CEN-04 / AD-108: the rate is read from the registry, not from the producer module
        run = self._run(labour_rate_source="registry", labour_driver_id=self.hours_drv.id)
        sheet = self._sheet(run, self.j1)
        self.assertEqual(sheet.labour_cost, 1675.0)
        self.assertEqual(set(sheet.labour_ids.mapped("rate_origin")), {"registry"})
        self.assertFalse(sheet.labour_ids.category_id)

    def test_overhead_from_registry(self):
        # FC-06 / AD-108: predetermined center rates x hours of the job on that center
        run = self._run(overhead_source="registry", overhead_driver_id=self.oh_drv.id, overhead_rate=0.0)
        self.assertEqual(self._sheet(run, self.j1).overhead_applied, 460.0)
        self.assertEqual(self._sheet(run, self.j2).overhead_applied, 96.0)
        self.assertEqual(run.overhead_applied, 556.0)

    def test_registry_labour_needs_driver(self):
        with self.assertRaises(ValidationError):
            self._run(compute=False, labour_rate_source="registry")

    def test_registry_overhead_needs_driver(self):
        with self.assertRaises(ValidationError):
            self._run(compute=False, overhead_source="registry")

    def test_registry_overhead_only_on_hours(self):
        with self.assertRaises(ValidationError):
            self._run(compute=False, overhead_source="registry", overhead_driver_id=self.oh_drv.id,
                      overhead_base="direct_cost")

    def test_registry_missing_rate_fails_loud(self):
        run = self._run(compute=False, labour_rate_source="registry", labour_driver_id=self.hours_drv.id,
                        date_to=date(2026, 4, 30))
        with self.assertRaisesRegex(UserError, "WP05 Z"):
            run.action_compute()

    def test_usage_blocks_producer_reset(self):
        # AD-002: the producer of a rate the job read cannot be reopened under it
        run = self._run(labour_rate_source="registry", labour_driver_id=self.hours_drv.id)
        usage = self.env["cmc.run.usage"].search([
            ("consumer_model", "=", "cmc.job.cost.run"), ("consumer_id", "=", run.id),
            ("origin_model", "=", "cmc.closing.run"), ("origin_id", "=", self.producer.id),
        ])
        self.assertEqual(len(usage), 1)
        with self.assertRaises(UserError):
            self.producer.action_reset_to_draft()
        run.action_reset_to_draft()
        self.producer.action_reset_to_draft()
        self.assertEqual(self.producer.state, "draft")

    def test_table_rates_published(self):
        # CEN-04 / AD-108: the resource rates applied become registry rates other engines can read
        run = self._run(labour_driver_id=self.hours_drv.id)
        rates = self._published(run)
        self.assertEqual(
            sorted((r.holder_model, r.holder_id, r.driver_id.id, r.date, r.component, r.value) for r in rates),
            sorted([
                ("account.analytic.account", self.z.id, self.hours_drv.id, D_FROM, "total", 40.0),
                ("account.analytic.account", self.y.id, self.hours_drv.id, D_FROM, "total", 60.0),
            ]),
        )
        found = self.env["cmc.rate"]._cmc_lookup(self.y, self.hours_drv, D_FROM, origin=run, company=self.company_a)
        self.assertEqual(found.value, 60.0)

    def test_no_publish_without_driver(self):
        self.assertFalse(self._published(self._run()))

    def test_reset_removes_published_rates(self):
        # AD-001/108: published rates are result rows of the run
        run = self._run(labour_driver_id=self.hours_drv.id)
        run.action_reset_to_draft()
        self.assertFalse(self._published(run))
