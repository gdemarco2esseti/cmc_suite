"""WP-11 real scenario evaluator ``cvr`` (AD-102): price, volume, unit variable cost and fixed cost of the latest
CVR analysis of the version company starting inside the version span; amount = operating result
(price - variable_unit) x volume - fixed_cost on (center, False, month of the analysis start). Pure evaluate.
"""

from datetime import date

from odoo.tests.common import TransactionCase, tagged

KEY = "cvr"
Y = 2041


# post_install: the other-company case creates a res.company, which needs the full registry (account fields on partners)
@tagged("post_install", "-at_install")
class TestCvrScenarioEvaluator(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "CVR SCN center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.version = cls._version("CVR SCN %s" % Y)
        cls.cvr = cls._cvr(date(Y, 3, 1), date(Y, 3, 31))

    @classmethod
    def _version(cls, name, company=None):
        return cls.env["cmc.plan.version"].create({
            "name": name, "version_type": "budget", "date_from": date(Y, 1, 1), "date_to": date(Y, 12, 31),
            "company_id": (company or cls.company).id,
        })

    @classmethod
    def _cvr(cls, date_from, date_to, price=10.0, var=6.0, volume=1000.0, fixed=3000.0, center=None, company=None):
        return cls.env["cmc.cvr.analysis"].create({
            "name": "CVR SCN", "mode": "simulazione", "center_id": (center or cls.center).id if center is not False else False,
            "date_from": date_from, "date_to": date_to, "price_unit": price, "variable_unit": var, "volume": volume,
            "fixed_cost": fixed, "company_id": (company or cls.company).id,
        })

    def _prepare(self, version=None):
        version = version or self.version
        return version._cmc_scenario_evaluator(KEY)["prepare"](version)

    def test_registered_in_hook(self):
        # AD-102: cmc_direct_costing registers its evaluator in the cmc_base hook
        self.assertIn(KEY, [entry["key"] for entry in self.version._cmc_scenario_evaluators()])

    def test_prepare_inputs(self):
        # inputs are the four CVR levers of the matching analysis
        self.assertEqual(self._prepare()["inputs"], {"price": 10.0, "volume": 1000.0, "variable_unit": 6.0, "fixed_cost": 3000.0})

    def test_evaluate_base_is_operating_result(self):
        # (10 - 6) x 1,000 - 3,000 = 1,000 on (center, no element, March)
        prepared = self._prepare()
        out = prepared["evaluate"](prepared["inputs"])
        self.assertEqual(out, {(self.center.id, False, date(Y, 3, 1)): 1000.0})
        cvr = self.cvr
        self.assertAlmostEqual(sum(out.values()), (cvr.price_unit - cvr.variable_unit) * cvr.volume - cvr.fixed_cost)
        self.assertAlmostEqual(sum(out.values()), cvr.mc_unit * cvr.volume - cvr.fixed_cost)

    def test_changed_inputs_expected_delta(self):
        # volume +100 -> +100 x MC (400); price +1 -> +volume (1,000); fixed +500 -> -500; var +1 -> -1,000
        prepared = self._prepare()
        inputs, evaluate = prepared["inputs"], prepared["evaluate"]
        base = sum(evaluate(inputs).values())
        for path, new, delta in (("volume", 1100.0, 400.0), ("price", 11.0, 1000.0), ("fixed_cost", 3500.0, -500.0), ("variable_unit", 7.0, -1000.0)):
            with self.subTest(path=path):
                self.assertAlmostEqual(sum(evaluate({**inputs, path: new}).values()) - base, delta)

    def test_evaluate_is_pure(self):
        # AD-114: trials never hit the DB
        prepared = self._prepare()
        self.env.flush_all()
        with self.assertQueryCount(0):
            for _ in range(50):
                prepared["evaluate"](prepared["inputs"])

    def test_latest_matching_analysis_wins(self):
        # the analysis with the latest end inside the version span is read
        later = self._cvr(date(Y, 6, 1), date(Y, 6, 30), price=12.0)
        prepared = self._prepare()
        self.assertEqual(prepared["inputs"]["price"], 12.0)
        self.assertEqual(list(prepared["evaluate"](prepared["inputs"])), [(self.center.id, False, later.date_from)])

    def test_analysis_outside_span_or_other_company_ignored(self):
        # a version with no analysis starting in its span (or only of another company) -> no input, profit 0
        other = self.env["res.company"].create({"name": "CVR SCN other co"})
        self._cvr(date(Y + 1, 2, 1), date(Y + 1, 2, 28), center=False, company=other)
        version = self.env["cmc.plan.version"].create({
            "name": "CVR SCN next", "version_type": "budget", "date_from": date(Y + 1, 1, 1), "date_to": date(Y + 1, 12, 31),
            "company_id": self.company.id,
        })
        self._cvr(date(Y, 12, 1), date(Y + 1, 1, 31))  # starts before the span
        prepared = self._prepare(version)
        self.assertEqual(prepared["inputs"], {})
        self.assertEqual(prepared["evaluate"]({}), {})
