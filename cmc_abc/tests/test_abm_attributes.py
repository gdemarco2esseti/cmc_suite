"""WP-08 / ABM-02 - attribute roll-up (VA/BVA/NVA down to the cost objects), Pareto, quadrant / pivot.

CONTRACT (see abm_common)
- Snapshots on ``cmc.abc.activity.cost``: ``va_class_value_id``, ``coq_value_id``, ``efficiency_value_id``;
  Pareto fields ``pareto_rank``, ``cost_share``, ``cumulative_share``, ``pareto_class`` (A <= 80, B <= 95, C).
- ``run._cmc_activity_pareto()``, ``run._cmc_activity_cost_quadrant(x, y)``, ``run._cmc_object_cost_by_attribute(a)``;
  ``cmc.abc.result.nva_cost``.
Hand figures: activity roll-up VA 6600 / NVA 5900 / BVA 4000. Charged to objects: P1 SETUP 2190 (NVA) + MACH 3768
(VA) + QC 1500 (NVA) -> VA 3768 / NVA 3690; P2 1460 + 2512 + 1000 -> VA 2512 / NVA 2460; C1 CUST 700 BVA.
Pareto (Σ 16500): MACH 40.0 / 40.0 A; SETUP 20.6061 / 60.6061 A; QC 15.1515 / 75.7576 A; MGMT 12.1212 / 87.8788 B;
ADMIN 6.0606 / 93.9394 B (tie with CUST broken by code WP07ADM < WP07CUST); CUST 6.0606 / 100 C.
Quadrant importance x performance: HH 7600, HL 3400, LL 2500, LH 1000, (H, none) 2000.
"""

from odoo.tests.common import tagged

from .abm_common import AbmCase


@tagged("post_install", "-at_install")
class TestAbmAttributes(AbmCase):
    def test_abm02_activity_cost_by_value_added_class(self):
        # ABM-02 / Turney Ch.7: cost per attribute value, Σ = total activity cost (existing roll-up, now on real classes)
        run = self._main_run()
        split = run._cmc_activity_cost_by_attribute("VA_CLASS")
        self.assertAlmostEqual(split[self.va.id], 6600.0, places=2)
        self.assertAlmostEqual(split[self.nva.id], 5900.0, places=2)
        self.assertAlmostEqual(split[self.bva.id], 4000.0, places=2)
        self.assertNotIn(False, split)

    def test_abm02_nva_cost_rolled_to_products(self):
        # ABM-02: the waste of each product = cost charged by its NVA activities
        run = self._main_run()
        split = run._cmc_object_cost_by_attribute("VA_CLASS")
        self.assertEqual(set(split[self.obj_p1.id]), {self.va.id, self.nva.id})
        self.assertAlmostEqual(split[self.obj_p1.id][self.va.id], 3768.0, places=2)
        self.assertAlmostEqual(split[self.obj_p1.id][self.nva.id], 3690.0, places=2)
        self.assertAlmostEqual(split[self.obj_p2.id][self.nva.id], 2460.0, places=2)
        self.assertAlmostEqual(split[self.obj_c1.id][self.bva.id], 700.0, places=2)
        self.assertAlmostEqual(sum(split[self.obj_p1.id].values()), self._res(run, self.obj_p1).activity_cost, places=2)

    def test_abm02_nva_cost_stored_on_results(self):
        # ABM-02: NVA cost per product is a stored figure (list / pivot without recomputing the roll-up)
        run = self._main_run()
        self.assertRecordValues(
            self._res(run, self.obj_p1) | self._res(run, self.obj_p2) | self._res(run, self.obj_c1),
            [{"nva_cost": 3690.0}, {"nva_cost": 2460.0}, {"nva_cost": 0.0}],
        )

    def test_abm02_reassigned_cost_takes_the_class_of_the_receiver(self):
        # ABM-02: ADMIN (BVA) reassigned into SETUP/MACH is charged to P1 as NVA/VA (class of the charging activity)
        run = self._main_run()
        split = run._cmc_object_cost_by_attribute("VA_CLASS")
        self.assertNotIn(self.bva.id, split[self.obj_p1.id])

    def test_abm02_attribute_snapshot_on_activity_costs(self):
        # ABM-02: classes are frozen at compute: reclassifying the activity later does not rewrite history
        run = self._main_run()
        qc = self._act(run, self.qc)
        self.assertRecordValues(qc, [{
            "va_class_value_id": self.nva.id, "coq_value_id": self.coq_appr.id, "efficiency_value_id": self.e2.id,
        }])
        self.qc.cmc_attribute_value_ids = [(3, self.nva.id), (4, self.bva.id)]
        self.assertEqual(qc.va_class_value_id, self.nva)
        self.assertEqual(self._act(run, self.mgmt).efficiency_value_id, self.env["cmc.attribute.value"])

    def test_abm02_pareto_ranking_and_classes(self):
        # ABM-02 / Turney Ch.8: rank by cost pool, cumulative %, 80/95 classes
        run = self._main_run()
        pareto = run._cmc_activity_pareto()
        self.assertEqual([row["activity"] for row in pareto], [self.mach, self.setup, self.qc, self.mgmt, self.admin, self.cust])
        self.assertEqual([row["rank"] for row in pareto], [1, 2, 3, 4, 5, 6])
        self.assertEqual([row["pareto_class"] for row in pareto], ["A", "A", "A", "B", "B", "C"])
        for row, share, cumulative in zip(
            pareto, (40.0, 20.6061, 15.1515, 12.1212, 6.0606, 6.0606), (40.0, 60.6061, 75.7576, 87.8788, 93.9394, 100.0)
        ):
            self.assertAlmostEqual(row["share"], share, places=3)
            self.assertAlmostEqual(row["cumulative_share"], cumulative, places=3)
        self.assertAlmostEqual(pareto[0]["cost"], 6600.0, places=2)

    def test_abm02_pareto_stored_for_the_graph(self):
        # ABM-02: the Pareto graph reads stored fields on the activity costs
        run = self._main_run()
        self.assertRecordValues(self._act(run, self.mgmt) | self._act(run, self.cust), [
            {"pareto_rank": 4, "pareto_class": "B"},
            {"pareto_rank": 6, "pareto_class": "C"},
        ])
        self.assertAlmostEqual(self._act(run, self.qc).cumulative_share, 75.7576, places=3)
        self.assertAlmostEqual(self._act(run, self.mach).cost_share, 40.0, places=3)

    def test_abm02_quadrant_of_two_user_attributes(self):
        # ABM-02 / Cokins MIW ch.4: importance x performance quadrant, cost as bubble size
        run = self._main_run()
        quadrant = run._cmc_activity_cost_quadrant(self.importance, "WP08PERF")
        expected = {
            (self.imp_h.id, self.perf_h.id): 7600.0, (self.imp_h.id, self.perf_l.id): 3400.0,
            (self.imp_l.id, self.perf_l.id): 2500.0, (self.imp_l.id, self.perf_h.id): 1000.0,
            (self.imp_h.id, False): 2000.0,
        }
        self.assertEqual(set(quadrant), set(expected))
        for key, amount in expected.items():
            self.assertAlmostEqual(quadrant[key], amount, places=2, msg=key)
        self.assertAlmostEqual(sum(quadrant.values()), run.total_activity_cost, places=2)

    def test_abm02_quadrant_as_pivot(self):
        # ABM-02: VA class x efficiency as a pivot on the stored snapshots
        run = self._main_run()
        groups = self.env["cmc.abc.activity.cost"]._read_group(
            [("run_id", "=", run.id)], ["va_class_value_id", "efficiency_value_id"], ["resource_cost:sum"]
        )
        pivot = {(va.id, eff.id): cost for va, eff, cost in groups}
        self.assertAlmostEqual(pivot[(self.va.id, self.e4.id)], 6600.0, places=2)
        self.assertAlmostEqual(pivot[(self.nva.id, self.e2.id)], 5900.0, places=2)
        self.assertAlmostEqual(pivot[(self.bva.id, self.e3.id)], 1000.0, places=2)
        self.assertAlmostEqual(pivot[(self.bva.id, self.e4.id)], 1000.0, places=2)
        self.assertAlmostEqual(pivot[(self.bva.id, False)], 2000.0, places=2)
