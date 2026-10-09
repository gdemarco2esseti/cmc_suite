"""WP-11 end-to-end Monte Carlo with the REAL evaluators of the producers (AD-102): ``budget_lines``
(cmc_budget) and ``cvr`` (cmc_direct_costing). cmc_scenario depends on cmc_base only: skipped when the producer is
not installed.

Budget hand case: revenue 5,000 (normal mean 5,000) - cost 1,000 (uniform 800..1,200) -> mean profit 4,000.
CVR hand case: (price uniform 9..11 - 6) x volume triangular(800, 1,000, 1,200) - 3,000 -> mean 1,000 (independent).
"""

from datetime import date
from unittest import SkipTest

from odoo.tests.common import TransactionCase, tagged


class RealEvaluatorCase(TransactionCase):
    _producer = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        state = cls.env["ir.module.module"].search([("name", "=", cls._producer)]).state
        if state != "installed":
            raise SkipTest("%s is not installed" % cls._producer)
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "WP11 real center", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )

    @classmethod
    def _version(cls, year):
        return cls.env["cmc.plan.version"].create({
            "name": "WP11 real %s" % year, "version_type": "budget", "date_from": date(year, 1, 1), "date_to": date(year, 12, 31),
        })

    def _simulation(self, version, key, variables, seed=7):
        sim = self.env["cmc.scenario.simulation"].create({
            "name": "WP11 real", "version_id": version.id, "evaluator_key": key, "trials": 2000, "seed": seed, "sampling": "lhs",
            "variable_ids": [
                (0, 0, {"sequence": i, "name": path, "input_path": path, "distribution": dist, **params})
                for i, (path, dist, params) in enumerate(variables)
            ],
        })
        sim.action_compute()
        return sim


@tagged("post_install", "-at_install")
class TestRealBudgetEvaluator(RealEvaluatorCase):
    _producer = "cmc_budget"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Element = cls.env["cmc.cost.element"]
        cls.el_cost = Element.create({"name": "WP11 real cost", "code": "WP11RC", "company_id": cls.env.company.id})
        cls.el_rev = Element.create({"name": "WP11 real revenue", "code": "WP11RR", "company_id": cls.env.company.id})
        cls.version = cls._version(2034)
        cls.env["cmc.budget.line"].create([
            {"version_id": cls.version.id, "kind": "cost", "center_id": cls.center.id, "element_id": cls.el_cost.id,
             "date": date(2034, 1, 1), "figure": 1000.0},
            {"version_id": cls.version.id, "kind": "revenue", "center_id": cls.center.id, "element_id": cls.el_rev.id,
             "date": date(2034, 1, 1), "figure": 5000.0},
        ])
        cls.cost_path = "%s/%s/2034-01/cost" % (cls.center.id, cls.el_cost.id)
        cls.rev_path = "%s/%s/2034-01/revenue" % (cls.center.id, cls.el_rev.id)

    def _variables(self):
        return [
            (self.rev_path, "normal", {"mean": 5000.0, "stdev": 500.0}),
            (self.cost_path, "uniform", {"low": 800.0, "high": 1200.0}),
        ]

    def test_monte_carlo_budget_lines(self):
        # RSK-01 on the real budget evaluator: base = budget profit, mean within LHS tolerance of the hand value
        sim = self._simulation(self.version, "budget_lines", self._variables())
        self.assertEqual(sim.state, "done")
        self.assertAlmostEqual(sim.base_output, 4000.0)
        self.assertAlmostEqual(sim.mean_output, 4000.0, delta=10)
        self.assertAlmostEqual(sim.stdev_output, (500.0 ** 2 + 400.0 ** 2 / 12) ** 0.5, delta=15)
        self.assertEqual(sum(sim.bin_ids.mapped("count")), 2000)

    def test_monte_carlo_budget_lines_deterministic(self):
        # same inputs and seed -> identical outputs
        first = self._simulation(self.version, "budget_lines", self._variables())
        second = self._simulation(self.version, "budget_lines", self._variables())
        self.assertEqual(
            (first.mean_output, first.stdev_output, first.p5_output, first.p95_output),
            (second.mean_output, second.stdev_output, second.p5_output, second.p95_output),
        )


@tagged("post_install", "-at_install")
class TestRealCvrEvaluator(RealEvaluatorCase):
    _producer = "cmc_direct_costing"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version = cls._version(2042)
        cls.env["cmc.cvr.analysis"].create({
            "name": "WP11 real CVR", "mode": "simulazione", "center_id": cls.center.id,
            "date_from": date(2042, 3, 1), "date_to": date(2042, 3, 31),
            "price_unit": 10.0, "variable_unit": 6.0, "volume": 1000.0, "fixed_cost": 3000.0,
        })

    def _variables(self):
        return [
            ("price", "uniform", {"low": 9.0, "high": 11.0}),
            ("volume", "triangular", {"low": 800.0, "mode": 1000.0, "high": 1200.0}),
        ]

    def test_monte_carlo_cvr(self):
        # RSK-01 on the real CVR evaluator: base = operating result, mean = (E[p] - v) x E[Q] - CF
        sim = self._simulation(self.version, "cvr", self._variables())
        self.assertEqual(sim.state, "done")
        self.assertAlmostEqual(sim.base_output, 1000.0)
        self.assertAlmostEqual(sim.mean_output, 1000.0, delta=20)
        # tornado at +/-10%: price swing = 2 x 1 x 1,000 = 2,000 > volume swing = 2 x 4 x 100 = 800
        top = sim.tornado_ids.filtered(lambda row: row.rank == 1)
        self.assertEqual(top.input_path, "price")
        self.assertAlmostEqual(top.swing, 2000.0)

    def test_monte_carlo_cvr_deterministic(self):
        # same inputs and seed -> identical outputs
        first = self._simulation(self.version, "cvr", self._variables())
        second = self._simulation(self.version, "cvr", self._variables())
        self.assertEqual((first.mean_output, first.p50_output), (second.mean_output, second.p50_output))
