"""WP-08 / ABM-01 - process (horizontal) view and activity x department matrix reconciling with the vertical view.

CONTRACT (see abm_common)
- ``cmc.abc.flow.center_id`` stored: center of origin of resource / tag / trace flows.
- ``run._cmc_activity_center_matrix(by="activity"|"process")`` -> {(activity|process id, center id): amount};
  rows = stage-1 cost of the activity / process, columns = what the center fed in, Σ = total_activity_cost.
Hand figures (main fixture): Z: SETUP 3400 (R1 3000 + R2 400), MACH 6600 (5000 + 1600), QC 2500 (2000 + tag
500); S: ADMIN 1000, CUST 1000, MGMT 2000. Fragmented variant (R3: ADMIN 25, CUST 15, QC 10, MGMT 50):
QC = Z 2500 + S 400 = 2900, CUST 600 -> process MFG = Z 12500 + S 400, SUP = S 3600 (Kaplan Ch.8: one activity
spread over several departments).
"""

from odoo.tests.common import tagged

from .abm_common import AbmCase


@tagged("post_install", "-at_install")
class TestAbmProcess(AbmCase):
    def _fragmented_run(self):
        lines = [line for line in self._resource_lines() if line["resource_id"] != self.r3.id] + [
            {"resource_id": self.r3.id, "activity_id": self.admin.id, "percent": 25},
            {"resource_id": self.r3.id, "activity_id": self.cust.id, "percent": 15},
            {"resource_id": self.r3.id, "activity_id": self.qc.id, "percent": 10},
            {"resource_id": self.r3.id, "activity_id": self.mgmt.id, "percent": 50},
        ]
        return self._main_run(resource_lines=lines)

    def test_abm01_flows_carry_the_center_of_origin(self):
        # ABM-01: the department dimension of an activity cost is the center its resources came from
        run = self._main_run()
        tag = run.flow_ids.filtered(lambda f: f.stage == "tag")
        self.assertEqual(tag.center_id, self.z)
        r3 = run.flow_ids.filtered(lambda f: f.stage == "resource" and f.source_resource_id == self.r3)
        self.assertEqual(r3.center_id, self.s)
        trace = run.flow_ids.filtered(lambda f: f.stage == "trace")
        self.assertEqual(trace.center_id, self.z)

    def test_abm01_activity_by_center_matrix(self):
        # ABM-01 / Kaplan Ch.8: activity x department matrix of the stage-1 cost
        matrix = self._main_run()._cmc_activity_center_matrix()
        expected = {
            (self.setup.id, self.z.id): 3400.0, (self.mach.id, self.z.id): 6600.0, (self.qc.id, self.z.id): 2500.0,
            (self.admin.id, self.s.id): 1000.0, (self.cust.id, self.s.id): 1000.0, (self.mgmt.id, self.s.id): 2000.0,
        }
        self.assertEqual(set(matrix), set(expected))
        for key, amount in expected.items():
            self.assertAlmostEqual(matrix[key], amount, places=2, msg=key)

    def test_abm01_fragmented_activity_spans_two_departments(self):
        # ABM-01 / Kaplan Ch.8: the matrix exposes an activity performed in several departments
        run = self._fragmented_run()
        matrix = run._cmc_activity_center_matrix()
        self.assertAlmostEqual(matrix[(self.qc.id, self.z.id)], 2500.0, places=2)
        self.assertAlmostEqual(matrix[(self.qc.id, self.s.id)], 400.0, places=2)
        self.assertAlmostEqual(matrix[(self.cust.id, self.s.id)], 600.0, places=2)
        self.assertAlmostEqual(self._act(run, self.qc).resource_cost, 2900.0, places=2)

    def test_abm01_process_by_center_matrix(self):
        # ABM-01 / Cokins MIW ch.3: process cost = Σ activity cost, split by department
        run = self._fragmented_run()
        matrix = run._cmc_activity_center_matrix(by="process")
        self.assertEqual(set(matrix), {(self.pr_mfg.id, self.z.id), (self.pr_mfg.id, self.s.id), (self.pr_sup.id, self.s.id)})
        self.assertAlmostEqual(matrix[(self.pr_mfg.id, self.z.id)], 12500.0, places=2)
        self.assertAlmostEqual(matrix[(self.pr_mfg.id, self.s.id)], 400.0, places=2)
        self.assertAlmostEqual(matrix[(self.pr_sup.id, self.s.id)], 3600.0, places=2)

    def test_abm01_horizontal_view_reconciles_with_vertical_view(self):
        # ABM-01: rows = process roll-up, columns = center feed, grand total = total activity cost
        run = self._fragmented_run()
        matrix = run._cmc_activity_center_matrix(by="process")
        processes = run._cmc_process_costs()
        for process_id, cost in processes.items():
            row = sum(v for (p, _c), v in matrix.items() if p == process_id)
            self.assertAlmostEqual(row, cost, places=2, msg=process_id)
        z_col = sum(v for (_p, c), v in matrix.items() if c == self.z.id)
        s_col = sum(v for (_p, c), v in matrix.items() if c == self.s.id)
        self.assertAlmostEqual(z_col, 12500.0, places=2)  # R1 10000 + R2 2000 + tag 500
        self.assertAlmostEqual(s_col, 4000.0, places=2)  # R3
        self.assertAlmostEqual(sum(matrix.values()), run.total_activity_cost, places=2)
        self.assertAlmostEqual(run.total_activity_cost, 16500.0, places=2)

    def test_abm01_matrix_as_pivot_on_flows(self):
        # ABM-01: the matrix is also a pivot of the stored flows (center x activity), same figures
        run = self._main_run()
        groups = self.env["cmc.abc.flow"]._read_group(
            [("run_id", "=", run.id), ("stage", "in", ("resource", "tag"))],
            ["center_id", "target_activity_id"], ["amount:sum"],
        )
        pivot = {(center.id, activity.id): amount for center, activity, amount in groups}
        self.assertAlmostEqual(pivot[(self.z.id, self.qc.id)], 2500.0, places=2)
        self.assertAlmostEqual(pivot[(self.s.id, self.mgmt.id)], 2000.0, places=2)
        self.assertAlmostEqual(sum(pivot.values()), 16500.0, places=2)
