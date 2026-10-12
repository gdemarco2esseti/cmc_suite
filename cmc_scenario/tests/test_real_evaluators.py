"""WP-11 end-to-end Monte Carlo with the REAL evaluators of the producers (AD-102): ``budget_lines``
(cmc_budget) and ``cvr`` (cmc_direct_costing). cmc_scenario depends on cmc_base only: skipped when the producer is
not installed.

Budget hand case: revenue 5,000 (normal mean 5,000) - cost 1,000 (uniform 800..1,200) -> mean profit 4,000.
CVR hand case: (price uniform 9..11 - 6) x volume triangular(800, 1,000, 1,200) - 3,000 -> mean 1,000 (independent).

Ronin WP-11 (CONTRACT in common.py): RN-001 ``cvr`` is ``versioned: False`` -> comparison and consolidation refuse
it (UserError naming the evaluator), simulation accepts it; RN-002 its outputs are in version currency: on a version
at currency_rate 2.0 the engine's base_output is the company CVR result (1,000), not twice it; RN-003 a
multi-product CVR analysis is simulated on its per-line inputs (``line/<id>/price``...).
"""

from datetime import date
from unittest import SkipTest

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from .common import EVALUATOR_KEY, PRICE, ScenarioCase


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

    # ---- Ronin RN-001 / RN-002 / RN-003 ---------------------------------------

    def _approved_cvr_version(self, year, **kw):
        version = self.env["cmc.plan.version"].create({
            "name": "WP11 real CVR %s" % year, "version_type": "budget", "date_from": date(year, 1, 1),
            "date_to": date(year, 12, 31), **kw,
        })
        version.action_approve()
        return version

    def test_rn001_comparison_refuses_cvr(self):
        # RN-001: two versions read the same CVR analysis -> a delta of 0 would look like "no change": refused
        name = self.version._cmc_scenario_evaluator("cvr")["name"]
        self.version.action_approve()
        scenario = self.version._cmc_scenario_copy()
        with self.assertRaises(UserError) as caught:
            run = self.env["cmc.scenario.comparison"].create({"name": "WP11 real cmp", "scenario_id": scenario.id, "evaluator_key": "cvr"})
            run.action_compute()
        self.assertIn(name, str(caught.exception))

    def test_rn001_consolidation_refuses_cvr(self):
        # RN-001: summing children that all read the same company CVR analysis double counts it: refused
        name = self.version._cmc_scenario_evaluator("cvr")["name"]
        self.version.action_approve()
        parent = self._version(2043)
        with self.assertRaises(UserError) as caught:
            run = self.env["cmc.scenario.consolidation"].create({
                "name": "WP11 real cons", "version_id": parent.id, "child_version_ids": [(6, 0, self.version.ids)], "evaluator_key": "cvr",
            })
            run.action_compute()
        self.assertIn(name, str(caught.exception))

    def test_rn002_simulation_base_output_in_company_currency(self):
        # RN-002: version at currency_rate 2.0 on a CVR result of 1,000 company -> base_output 1,000 (evaluate 500 x 2.0)
        version = self.env["cmc.plan.version"].create({
            "name": "WP11 real CVR rate", "version_type": "budget", "date_from": date(2044, 1, 1), "date_to": date(2044, 12, 31),
            "currency_rate": 2.0,
        })
        self.env["cmc.cvr.analysis"].create({
            "name": "WP11 real CVR rate", "mode": "simulazione", "center_id": self.center.id,
            "date_from": date(2044, 3, 1), "date_to": date(2044, 3, 31),
            "price_unit": 10.0, "variable_unit": 6.0, "volume": 1000.0, "fixed_cost": 3000.0,
        })
        sim = self._simulation(version, "cvr", [("volume", "triangular", {"low": 800.0, "mode": 1000.0, "high": 1200.0})])
        self.assertAlmostEqual(sim.base_output, 1000.0)
        # E[volume] = 1,000 -> mean 1,000 company, not 2,000
        self.assertAlmostEqual(sim.mean_output, 1000.0, delta=20)

    def test_rn003_simulation_on_multiproduct_mix(self):
        # RN-003: mix 100 x (20 - 12) + 200 x (10 - 7) - 1,000 = 400; price of line A uniform 18..22 -> mean 400,
        # tornado on that line price at +/-10% = 2 x 2 x 100 = 400
        version = self._version(2045)
        cvr = self.env["cmc.cvr.analysis"].create({
            "name": "WP11 real CVR mix", "mode": "simulazione", "center_id": self.center.id, "is_multiproduct": True,
            "date_from": date(2045, 3, 1), "date_to": date(2045, 3, 31), "fixed_cost": 1000.0,
            "line_ids": [(0, 0, {"qty": 100.0, "price_unit": 20.0, "variable_unit": 12.0}),
                         (0, 0, {"qty": 200.0, "price_unit": 10.0, "variable_unit": 7.0})],
        })
        path = "line/%s/price" % cvr.line_ids.sorted("id")[0].id
        sim = self._simulation(version, "cvr", [(path, "uniform", {"low": 18.0, "high": 22.0})])
        self.assertAlmostEqual(sim.base_output, 400.0)
        self.assertAlmostEqual(sim.mean_output, 400.0, delta=5)
        self.assertRecordValues(sim.tornado_ids, [{"input_path": path, "rank": 1}])
        self.assertAlmostEqual(sim.tornado_ids.swing, 400.0)


# post_install: the ScenarioCase users need the full registry (account fields on partners)
@tagged("post_install", "-at_install")
class TestNonVersionedEvaluator(ScenarioCase):
    """RN-001 on the engine, independent of the producers: a test evaluator declared ``versioned: False``."""

    FLAT_KEY = "wp11_flat"
    FLAT_NAME = "WP11 flat evaluator"

    def setUp(self):
        super().setUp()
        Version = type(self.env["cmc.plan.version"])
        patched = Version._cmc_scenario_evaluators
        entry = {"key": self.FLAT_KEY, "name": self.FLAT_NAME, "versioned": False, "prepare": self._prepare}

        def evaluators(model):
            return patched(model) + [entry]

        self.patch(Version, "_cmc_scenario_evaluators", evaluators)

    def test_rn001_comparison_refuses_non_versioned(self):
        # RN-001: comparison on a non-versioned evaluator -> UserError naming it, no line written
        scenario = self._scenario()
        with self.assertRaises(UserError) as caught:
            self._comparison(scenario, evaluator_key=self.FLAT_KEY).action_compute()
        self.assertIn(self.FLAT_NAME, str(caught.exception))
        self.assertFalse(self.env["cmc.scenario.comparison.line"].search([("comparison_id.scenario_id", "=", scenario.id)]))

    def test_rn001_comparison_versioned_still_allowed(self):
        # RN-001: the same engine with a versioned evaluator computes
        run = self._comparison(self._scenario(), evaluator_key=EVALUATOR_KEY)
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertAlmostEqual(run.baseline_profit, 1300.0)

    def test_rn001_consolidation_refuses_non_versioned(self):
        # RN-001: consolidation on a non-versioned evaluator -> UserError naming it, children not locked by usage
        parent = self._version("WP11 flat parent", {})
        with self.assertRaises(UserError) as caught:
            self._consolidation(parent, self.baseline, evaluator_key=self.FLAT_KEY).action_compute()
        self.assertIn(self.FLAT_NAME, str(caught.exception))

    def test_rn001_simulation_accepts_non_versioned(self):
        # RN-001: a simulation perturbs the inputs of one run: a non-versioned evaluator is fine
        path = self._path(self.center_a, PRICE)
        sim = self._simulation([(path, "uniform", {"low": 9.0, "high": 11.0})], evaluator_key=self.FLAT_KEY)
        sim.action_compute()
        self.assertEqual(sim.state, "done")
        self.assertAlmostEqual(sim.base_output, 1300.0)
