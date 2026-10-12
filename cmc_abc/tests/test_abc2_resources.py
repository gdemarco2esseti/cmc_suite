"""WP-07 / ABC-02, ABC-03 - resource layer from the ledger and resource drivers (stage 1).

CONTRACT (see abc2_common for the whole WP)
- ``cmc.abc.resource`` = center x elements pool (empty elements = whole center), basis percent / quantity;
  two active resources of a company overlapping on (center, element) -> ValidationError.
- Pool = cost-base lines of the run span/company on center x elements, F/V split, EXCLUDING v1 tagged lines
  (``cmc_activity_id``: they stay a direct charge of their activity, flow stage ``tag``) and stamped direct
  lines (cost object + ``cmc_directness = 'direct'``).
- ``resource_line_ids``: percent lines must total 100 per resource (UserError at Compute naming it);
  quantity lines split pro rata; a line to a process/task -> ValidationError.
- Flows stage ``resource``: one per resource x element x activity (``source_resource_id``,
  ``cost_element_id``, ``target_activity_id``, ``amount``, ``fixed_amount``, ``variable_amount``).
- Reconciliation: ``in_scope_cost`` (every cost-base line of the resource centers in span) =
  resources + tag pools + traced on those centers + ``unassigned_cost`` (reported, never charged).
Hand numbers (Cokins MIW 4-1: activity = Σ resource x driver%): R1 10000 -> 3000/5000/2000;
R2 2000 by 80/20 machine hours -> 1600/400; R3 4000 -> 1000/1000/2000; QC + 500 tag = 2500.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2Resources(Abc2Case):
    @staticmethod
    def _from(run, resource, activity=None, field="amount"):
        flows = run.flow_ids.filtered(
            lambda f: f.stage == "resource" and f.source_resource_id == resource
            and (activity is None or f.target_activity_id == activity)
        )
        return sum(flows.mapped(field))

    def test_abc02_pools_from_ledger(self):
        # ABC-02: pool = center x element cost of the span; the April 9999 and the tagged 500 stay out
        run = self._main_run()
        self.assertAlmostEqual(self._from(run, self.r1), 10000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r2), 2000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r3), 4000.0, places=2)
        self.assertAlmostEqual(run.total_resource_cost, 16500.0, places=2)  # 16000 resources + 500 tag

    def test_abc02_pool_split_fixed_variable(self):
        # ABC-02/ABC-10: the F/V nature of the ledger lines enters the model with the pool
        run = self._main_run()
        self.assertAlmostEqual(self._from(run, self.r1, field="fixed_amount"), 6000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r1, field="variable_amount"), 4000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r2, field="variable_amount"), 0.0, places=2)

    def test_abc02_whole_center_resource_keeps_elements(self):
        # Turney Ch.5 bill of costs: a whole-center resource still knows its elements (S: SAL 3000, OTH 1000)
        run = self._main_run()
        flows = run.flow_ids.filtered(lambda f: f.stage == "resource" and f.source_resource_id == self.r3
                                      and f.target_activity_id == self.admin)
        by_element = {f.cost_element_id: f.amount for f in flows}
        self.assertAlmostEqual(by_element[self.sal], 750.0, places=2)
        self.assertAlmostEqual(by_element[self.oth], 250.0, places=2)

    def test_abc02_traced_lines_not_in_resource_pool(self):
        # ABC-07 vs ABC-02: a stamped direct line on a resource center is traced, never pooled (no double count)
        self._cost(self.env_a, self.s, 150, self.oth, "variable", cost_object=self.obj_p1)
        run = self._main_run()
        self.assertAlmostEqual(self._from(run, self.r3), 4000.0, places=2)
        self.assertAlmostEqual(run.total_traced_cost, 650.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_p1).traced_cost, 450.0, places=2)

    def test_abc02_tag_pool_is_direct_charge(self):
        # backward compat: the v1 activity tag is a direct charge resource of its activity
        run = self._main_run()
        tag = run.flow_ids.filtered(lambda f: f.stage == "tag")
        self.assertEqual(tag.target_activity_id, self.qc)
        self.assertAlmostEqual(sum(tag.mapped("amount")), 500.0, places=2)
        self.assertAlmostEqual(self._act(run, self.qc).resource_cost, 2500.0, places=2)

    def test_abc02_reconciled_to_cost_base(self):
        # ABC-02: Σ resources reconciles to 100% of the in-scope cost (Z 13000 + S 4000)
        run = self._main_run()
        self.assertAlmostEqual(run.in_scope_cost, 17000.0, places=2)
        self.assertAlmostEqual(run.unassigned_cost, 0.0, places=2)

    def test_abc02_unassigned_cost_reported_not_charged(self):
        # ABC-02: a Z element no resource covers shows up as unassigned, outside every object
        self._cost(self.env_a, self.z, 700, self.oth, "fixed")
        run = self._main_run()
        self.assertAlmostEqual(run.in_scope_cost, 17700.0, places=2)
        self.assertAlmostEqual(run.unassigned_cost, 700.0, places=2)
        self.assertAlmostEqual(run.total_object_cost, 13930.0, places=2)

    def test_abc02_overlapping_resources_rejected(self):
        # ABC-02: one cost, one resource - an overlap would count Z salaries twice
        Res = self.env_a["cmc.abc.resource"]
        with self.assertRaises(ValidationError):
            Res.create({"name": "Z people bis", "code": "WP07R1B", "center_id": self.z.id,
                        "cost_element_ids": [(6, 0, self.sal.ids)]})
        with self.assertRaises(ValidationError):
            Res.create({"name": "Z all", "code": "WP07RZ", "center_id": self.z.id})

    def test_abc03_percent_matrix(self):
        # ABC-03: cost_A = Σ_r cost_r x driver%_r,A
        run = self._main_run()
        self.assertAlmostEqual(self._from(run, self.r1, self.setup), 3000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r1, self.mach), 5000.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r1, self.qc), 2000.0, places=2)
        self.assertAlmostEqual(self._act(run, self.setup).resource_cost, 3400.0, places=2)
        self.assertAlmostEqual(self._act(run, self.mach).resource_cost, 6600.0, places=2)
        self.assertAlmostEqual(run.total_activity_cost, 16500.0, places=2)

    def test_abc03_quantity_matrix(self):
        # ABC-03: measured usage (machine hours 80/20) splits the machine resource pro rata
        run = self._main_run()
        self.assertAlmostEqual(self._from(run, self.r2, self.mach), 1600.0, places=2)
        self.assertAlmostEqual(self._from(run, self.r2, self.setup), 400.0, places=2)

    def test_abc03_percent_must_total_100(self):
        # ABC-03: each resource is assigned 100%, never 90% (the missing 1000 would vanish)
        lines = self._resource_lines()
        lines[2]["percent"] = 10
        run = self._main_run(compute=False, resource_lines=lines)
        with self.assertRaisesRegex(UserError, "Z people"):
            run.action_compute()
        self.assertEqual(run.state, "draft")

    def test_abc03_resource_line_to_process_rejected(self):
        # ABC-01/03: only activity nodes carry cost; a process is a roll-up
        run = self._main_run(compute=False)
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abc.resource.line"].create(
                {"run_id": run.id, "resource_id": self.r3.id, "activity_id": self.pr_sup.id, "percent": 10}
            )
