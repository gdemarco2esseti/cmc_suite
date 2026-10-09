"""WP-16 / AD-114 - the Compute cost does not grow with the size of the sheet, the alternatives or the horizon.

CONTRACT
- Target sheet: the current cost of the product and of every ``registry`` component are read in one batch; the
  deployment is built in Python and its result rows created with one ``create(vals_list)``.
- Feature costing: the rates of all design variables in one batch, usages recorded in one call, snapshots
  written in batch, results/violations created in batch.
- LCC: driver rates in one batch; year and phase results created in one batch each; no per-year search.
  30 rows cost the same queries as 3 (small constant tolerance): a per-row search/create = N+1 = red.
"""

from odoo.tests.common import tagged

from .common import D_JAN, JAN, TlcCase

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestPerformance(TlcCase):
    def _assert_flat(self, small, big):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()

    def _registry_products(self, tag, n):
        products = self.env["product.product"].create([{"name": "WP16 vol %s %s" % (tag, i)} for i in range(n)])
        objects = self.env["cmc.cost.object"]._cmc_ensure_objects(products)
        self._publish(self.producer, [(obj, self.units, JAN, 10.0 + i) for i, obj in enumerate(objects)])
        return products

    def _target_sheet(self, n):
        target = self._target()
        functions = self.env["cmc.target.cost.function"].create(
            [{"target_id": target.id, "name": "F%s" % i, "importance_pct": 100.0 / n} for i in range(n)]
        )
        products = self._registry_products("tgt%s" % n, n)
        components = self.env["cmc.target.cost.component"].create([
            {"target_id": target.id, "name": "C%s" % i, "product_id": product.id, "cost_source": "registry"}
            for i, product in enumerate(products)
        ])
        # component i serves function i only: a diagonal matrix keeps every share at 100%
        self.env["cmc.target.cost.matrix"].create([
            {"target_id": target.id, "component_id": c.id, "function_id": f.id, "share_pct": 100.0}
            for c, f in zip(components, functions, strict=True)
        ])
        return target

    def _feature_costing(self, n):
        centers = self.env["account.analytic.account"].create([
            {"name": "WP16 vol fc %s-%s" % (n, i), "plan_id": self.plan.id, "cmc_center_type": "final"} for i in range(n)
        ])
        self._publish(self.producer, [(center, self.parts, JAN, 1.0 + i) for i, center in enumerate(centers)])
        costing, _alts = self._feature({
            "Alt %s" % i: [(center, self.parts, 3, None)] for i, center in enumerate(centers)
        })
        return costing

    def _lcc_model(self, n):
        centers = self.env["account.analytic.account"].create([
            {"name": "WP16 vol lcc %s-%s" % (n, i), "plan_id": self.plan.id, "cmc_center_type": "final"} for i in range(n)
        ])
        self._publish(self.producer, [(center, self.service_hours, JAN, 50.0 + i) for i, center in enumerate(centers)])
        costs = [self._cost("Y%s" % y, "production", "usual", y, 100.0) for y in range(n)]
        costs += [
            {"name": "S%s" % y, "phase": "use", "category": "hidden", "year": y, "holder_model": center._name,
             "holder_id": center.id, "driver_id": self.service_hours.id, "quantity": 2.0}
            for y, center in enumerate(centers)
        ]
        return self._lcc([(y, 100, 5.0, False) for y in range(n)], costs, horizon_years=n, discount_rate=8.0, rate_date=D_JAN)

    def test_target_compute_independent_of_components(self):
        # TGT-01 / AD-114: deploying to 30 functions/components costs the same queries as 3
        self._assert_flat(self._target_sheet(3), self._target_sheet(30))

    def test_feature_compute_independent_of_alternatives(self):
        # TGT-02 / AD-114: costing 30 alternatives costs the same queries as 3
        self._assert_flat(self._feature_costing(3), self._feature_costing(30))

    def test_lcc_compute_independent_of_horizon(self):
        # LCC-01/02 / AD-114: a 30-year life with driver lines costs the same queries as a 3-year one
        self._assert_flat(self._lcc_model(3), self._lcc_model(30))
