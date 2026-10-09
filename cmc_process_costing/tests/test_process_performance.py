"""WP-06 / AD-114 - the access cost of Prefill and Compute does not grow with the data (volume fixtures).

CONTRACT (part)
- Compute reads the department's analytic lines (unbounded: every MP/MOD/overhead posting of the month) with
  grouped reads at the element grain and creates its 2-3 lines in one batch: 60 more lines in the month must
  cost the same queries (small constant tolerance). A browse / loop over lines = red.
- Prefill reads produced and scrap quantities through the feeders (grouped ``_read_group``): 4 more MOs with scrap
  in the month must cost the same queries as 1.
"""

from odoo.tests.common import tagged

from .common import TODAY, ProcessCase

TOLERANCE = 4


@tagged("post_install", "-at_install")
class TestProcessPerformance(ProcessCase):
    def _measure(self, func):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        func()
        self.env.flush_all()
        return self.cr.sql_log_count - start

    def test_compute_independent_of_analytic_lines(self):
        # AD-114: a real month carries thousands of postings on a department
        small = self._d1(compute=False)
        budget = self._measure(small.action_compute) + TOLERANCE
        small.action_reset_to_draft()
        small.unlink()
        self.AL.create([
            {"name": "WP06 volume %s" % i, "account_id": self.d1.id, "amount": -10.0, "company_id": self.company.id,
             "cmc_cost_element_id": (self.mp if i % 2 else self.overhead).id, "cmc_behavior": "variable",
             "cmc_directness": "direct" if i % 2 else "indirect", "date": TODAY}
            for i in range(60)
        ])
        big = self._d1(compute=False)
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
        # the volume fixture is correct too: 30 x 10 more materials, 30 x 10 more conversion
        self.assertAlmostEqual(big.cost_to_account, 126100.0 + 600.0)

    def test_prefill_independent_of_mos(self):
        # AD-114: dozens of MOs and scraps per month must not cost one query batch each
        small = self._d1(prefill=False, compute=False)
        budget = self._measure(small.action_cmc_prefill) + TOLERANCE
        for _i in range(4):
            mo = self._done_mo(self.s, self.bom_s, 10.0, self.d1, duration=600.0)
            self._scrap(mo, self.s, 1.0)
        big = self._d1(prefill=False, compute=False)
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_cmc_prefill()
        self.assertRecordValues(big, [{"units_completed": 900.0 + 36.0, "units_spoiled": 104.0}])
