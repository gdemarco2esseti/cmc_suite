"""WP-08 / COQ-01 - cost of quality: prevention / appraisal / internal / external failure / error-free split
reconciled to the activity cost, trend by month, cost of poor quality per product.

CONTRACT (see abm_common)
- ``run._cmc_coq_split()`` from the ``coq_value_id`` snapshot of the activity costs; Σ of the six buckets = total.
- ``env["cmc.abc.run"]._cmc_coq_trend(date_from, date_to)`` -> [{month, **split}] (month figure rule).
- Run input ``defect_measure_id`` (statistical), results ``defect_qty`` / ``copq_amount`` = defects x unit cost,
  run ``copq_total``.
Hand figures (March): prevention SETUP 3400, appraisal QC 2500, internal ADMIN 1000, external CUST 1000,
error-free MACH 6600, unclassified MGMT 2000; conformance 5900, nonconformance 2000, COQ 7900 = 47.8788 % of 16500.
April: appraisal QC 9999 = total. CoPQ: P1 4 x 77.58 = 310.32, P2 2 x 103.44 = 206.88, total 517.20
(the April defect fact of P1 stays out).
"""

from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .abm_common import APR_TO, AbmCase
from .abc2_common import D_FROM

BUCKETS = ("prevention", "appraisal", "internal_failure", "external_failure", "error_free", "unclassified")


@tagged("post_install", "-at_install")
class TestAbmCoq(AbmCase):
    def test_coq01_split_by_category(self):
        # COQ-01 / Kaplan Ch.8: activity cost by prevention / appraisal / internal / external failure
        split = self._main_run()._cmc_coq_split()
        expected = {
            "prevention": 3400.0, "appraisal": 2500.0, "internal_failure": 1000.0, "external_failure": 1000.0,
            "error_free": 6600.0, "unclassified": 2000.0, "conformance": 5900.0, "nonconformance": 2000.0,
            "coq_total": 7900.0, "total": 16500.0,
        }
        for key, amount in expected.items():
            self.assertAlmostEqual(split[key], amount, places=2, msg=key)
        self.assertAlmostEqual(split["coq_percent"], 47.8788, places=3)

    def test_coq01_split_reconciles_to_total_activity_cost(self):
        # COQ-01: error-free and unclassified close the split, nothing is lost or counted twice
        run = self._main_run()
        split = run._cmc_coq_split()
        self.assertAlmostEqual(sum(split[key] for key in BUCKETS), run.total_activity_cost, places=2)
        self.assertAlmostEqual(split["total"], run.total_activity_cost, places=2)

    def test_coq01_split_uses_the_compute_snapshot(self):
        # COQ-01: reclassifying an activity after compute does not change a certified month
        run = self._main_run()
        self.qc.cmc_attribute_value_ids = [(3, self.coq_appr.id), (4, self.coq_int.id)]
        split = run._cmc_coq_split()
        self.assertAlmostEqual(split["appraisal"], 2500.0, places=2)
        self.assertAlmostEqual(split["internal_failure"], 1000.0, places=2)

    def test_coq01_trend_by_month(self):
        # COQ-01 / Kaplan Ch.8: compare cost of quality over time
        self._main_run()
        self._april_run()
        trend = self.env_a["cmc.abc.run"]._cmc_coq_trend(D_FROM, APR_TO)
        self.assertEqual([row["month"] for row in trend], [D_FROM, date(2027, 4, 1)])
        self.assertAlmostEqual(trend[0]["coq_total"], 7900.0, places=2)
        self.assertAlmostEqual(trend[1]["appraisal"], 9999.0, places=2)
        self.assertAlmostEqual(trend[1]["prevention"], 0.0, places=2)
        self.assertAlmostEqual(trend[1]["coq_percent"], 100.0, places=2)

    def test_coq01_trend_ignores_draft_and_other_company(self):
        # COQ-01: drafts never count; the trend is the current company's
        self._main_run()
        self._april_run(compute=False)
        other = self.env["res.company"].create({"name": "CMC WP08 coq other"})
        trend_other = self._company_env(other)["cmc.abc.run"]._cmc_coq_trend(D_FROM, APR_TO)
        self.assertEqual(trend_other, [])
        self.assertEqual(len(self.env_a["cmc.abc.run"]._cmc_coq_trend(D_FROM, APR_TO)), 1)

    def test_coq01_cost_of_poor_quality_per_product(self):
        # COQ-01 / Turney Ch.9: CoPQ = defect units x product ABC unit cost
        run = self._main_run(defect_measure_id=self.defects.id)
        self.assertRecordValues(self._res(run, self.obj_p1) | self._res(run, self.obj_p2), [
            {"defect_qty": 4.0, "copq_amount": 310.32},
            {"defect_qty": 2.0, "copq_amount": 206.88},
        ])
        self.assertAlmostEqual(run.copq_total, 517.20, places=2)

    def test_coq01_copq_reset(self):
        # COQ-01: CoPQ is a run result
        run = self._main_run(defect_measure_id=self.defects.id)
        run.action_reset_to_draft()
        self.assertEqual(run.copq_total, 0.0)

    def test_coq01_defect_measure_is_statistical(self):
        # COQ-01 / AD-107: defects are a statistical measure, not an allocation driver
        with self.assertRaises(ValidationError):
            self._main_run(compute=False, defect_measure_id=self.inspections.id)
