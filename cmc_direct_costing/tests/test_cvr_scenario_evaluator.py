"""WP-11 real scenario evaluator ``cvr`` (AD-102): price, volume, unit variable cost and fixed cost of the latest
CVR analysis of the version company starting inside the version span; amount = operating result
(price - variable_unit) x volume - fixed_cost on (center, False, month of the analysis start). Pure evaluate.

Ronin WP-11 (contract in cmc_scenario/tests/common.py):
- RN-001: the hook entry declares ``"versioned": False`` (the figures do not come from the version).
- RN-002: amounts in VERSION currency: monetary inputs (price, variable_unit, fixed_cost, per-line price and
  variable_unit) = company figure / ``version.currency_rate``; volume / qty untouched.
- RN-003: multi-product analysis (``is_multiproduct``): inputs ``line/<line id>/price``, ``line/<line id>/variable_unit``,
  ``line/<line id>/qty`` per mix line plus ``fixed_cost`` (no single-product lever); amount = sum qty x (p - v) - CF,
  the operating result of ``_cvp_figures`` on the same mix.
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

    # ---- Ronin RN-001 / RN-002 / RN-003 ---------------------------------------

    def test_rn001_cvr_is_not_versioned(self):
        # RN-001: the CVR analysis is not tied to a version -> the entry says so, the budget evaluator does not
        entry = self.version._cmc_scenario_evaluator(KEY)
        self.assertIn("versioned", entry)
        self.assertIs(entry["versioned"], False)
        for other in self.version._cmc_scenario_evaluators():
            with self.subTest(key=other["key"]):
                self.assertIn(other["versioned"], (True, False))

    def test_rn002_inputs_in_version_currency(self):
        # RN-002: rate 2.0 -> monetary levers halved, volume untouched; evaluate 500 (x 2.0 by the engine = 1,000)
        version = self.env["cmc.plan.version"].create({
            "name": "CVR SCN rate", "version_type": "budget", "date_from": date(Y + 2, 1, 1), "date_to": date(Y + 2, 12, 31),
            "company_id": self.company.id, "currency_rate": 2.0,
        })
        cvr = self._cvr(date(Y + 2, 3, 1), date(Y + 2, 3, 31))
        self.assertAlmostEqual(cvr._cmc_profit(), 1000.0)  # company currency
        prepared = self._prepare(version)
        self.assertEqual(prepared["inputs"], {"price": 5.0, "volume": 1000.0, "variable_unit": 3.0, "fixed_cost": 1500.0})
        out = prepared["evaluate"](prepared["inputs"])
        self.assertAlmostEqual(sum(out.values()), 500.0)
        self.assertAlmostEqual(version._cmc_to_company_currency(sum(out.values())), 1000.0)

    def test_rn002_rate_one_unchanged(self):
        # RN-002: rate 1.0 is the identity (default version)
        self.assertEqual(self.version.currency_rate, 1.0)
        prepared = self._prepare()
        self.assertAlmostEqual(sum(prepared["evaluate"](prepared["inputs"]).values()), 1000.0)

    def _mix_cvr(self, year, lines, fixed=1000.0):
        """lines = [(qty, price, variable)] of a multi-product analysis in March `year`."""
        return self.env["cmc.cvr.analysis"].create({
            "name": "CVR SCN mix", "mode": "simulazione", "center_id": self.center.id, "is_multiproduct": True,
            "date_from": date(year, 3, 1), "date_to": date(year, 3, 31), "fixed_cost": fixed,
            # single-product levers deliberately set: a multi-product analysis ignores them
            "price_unit": 99.0, "variable_unit": 1.0, "volume": 7.0,
            "line_ids": [(0, 0, {"qty": qty, "price_unit": price, "variable_unit": var}) for qty, price, var in lines],
            "company_id": self.company.id,
        })

    def _mix_version(self, year, rate=1.0):
        return self.env["cmc.plan.version"].create({
            "name": "CVR SCN mix %s" % year, "version_type": "budget", "date_from": date(year, 1, 1),
            "date_to": date(year, 12, 31), "company_id": self.company.id, "currency_rate": rate,
        })

    def test_rn003_multiproduct_inputs_per_line(self):
        # RN-003: one price / variable / qty input per mix line + fixed cost; the single-product levers are not inputs
        version = self._mix_version(Y + 3)
        cvr = self._mix_cvr(Y + 3, [(100.0, 20.0, 12.0), (200.0, 10.0, 7.0)])
        line_a, line_b = cvr.line_ids.sorted("id")
        self.assertEqual(self._prepare(version)["inputs"], {
            "line/%s/price" % line_a.id: 20.0, "line/%s/variable_unit" % line_a.id: 12.0, "line/%s/qty" % line_a.id: 100.0,
            "line/%s/price" % line_b.id: 10.0, "line/%s/variable_unit" % line_b.id: 7.0, "line/%s/qty" % line_b.id: 200.0,
            "fixed_cost": 1000.0,
        })

    def test_rn003_multiproduct_operating_result(self):
        # RN-003: 100 x (20 - 12) + 200 x (10 - 7) - 1,000 = 800 + 600 - 1,000 = 400, as _cvp_figures on the same mix
        version = self._mix_version(Y + 3)
        mix = [(100.0, 20.0, 12.0), (200.0, 10.0, 7.0)]
        cvr = self._mix_cvr(Y + 3, mix)
        prepared = self._prepare(version)
        out = prepared["evaluate"](prepared["inputs"])
        self.assertEqual(list(out), [(self.center.id, False, date(Y + 3, 3, 1))])
        self.assertAlmostEqual(sum(out.values()), 400.0)
        figures = cvr._cvp_figures(0.0, 0.0, 1000.0, 0.0, 0.0, mix, 0.01)
        tot_rev = sum(qty * price for qty, price, _var in mix)
        self.assertAlmostEqual(sum(out.values()), figures["mc_ratio"] * tot_rev - 1000.0)
        self.assertAlmostEqual(figures["operating_leverage"], 1400.0 / sum(out.values()))

    def test_rn003_multiproduct_line_price_delta(self):
        # RN-003: line price +2 -> output +qty x 2; line qty +10 -> +10 x MC of that line; fixed +100 -> -100
        version = self._mix_version(Y + 3)
        cvr = self._mix_cvr(Y + 3, [(100.0, 20.0, 12.0), (200.0, 10.0, 7.0)])
        line_a, line_b = cvr.line_ids.sorted("id")
        prepared = self._prepare(version)
        inputs, evaluate = prepared["inputs"], prepared["evaluate"]
        base = sum(evaluate(inputs).values())
        for path, new, delta in (
            ("line/%s/price" % line_a.id, 22.0, 200.0),
            ("line/%s/price" % line_b.id, 9.0, -200.0),
            ("line/%s/variable_unit" % line_b.id, 8.0, -200.0),
            ("line/%s/qty" % line_a.id, 110.0, 80.0),
            ("fixed_cost", 1100.0, -100.0),
        ):
            with self.subTest(path=path):
                self.assertAlmostEqual(sum(evaluate({**inputs, path: new}).values()) - base, delta)

    def test_rn003_multiproduct_version_currency(self):
        # RN-002 x RN-003: rate 2.0 -> per-line prices / variables and CF halved, qty untouched; evaluate 200
        version = self._mix_version(Y + 4, rate=2.0)
        cvr = self._mix_cvr(Y + 4, [(100.0, 20.0, 12.0), (200.0, 10.0, 7.0)])
        line_a = cvr.line_ids.sorted("id")[0]
        prepared = self._prepare(version)
        self.assertAlmostEqual(prepared["inputs"]["line/%s/price" % line_a.id], 10.0)
        self.assertAlmostEqual(prepared["inputs"]["line/%s/qty" % line_a.id], 100.0)
        self.assertAlmostEqual(prepared["inputs"]["fixed_cost"], 500.0)
        self.assertAlmostEqual(sum(prepared["evaluate"](prepared["inputs"]).values()), 200.0)

    def _prepare_queries(self, version):
        self.env.invalidate_all()
        before = self.env.cr.sql_log_count
        prepared = self._prepare(version)
        return self.env.cr.sql_log_count - before, prepared

    def test_rn003_multiproduct_cost_does_not_grow_with_lines(self):
        # costo d'accesso: the mix is a recordset sized by the data -> prepare reads the lines in a batch (same query
        # count with 2 and 60 lines) and evaluate stays pure on a 60-line mix
        small, large = self._mix_version(Y + 5), self._mix_version(Y + 6)
        self._mix_cvr(Y + 5, [(10.0, 5.0, 3.0)] * 2)
        self._mix_cvr(Y + 6, [(10.0 + i, 5.0 + i, 3.0) for i in range(60)])
        self.env.flush_all()
        count_small, _prepared = self._prepare_queries(small)
        count_large, prepared = self._prepare_queries(large)
        self.assertEqual(len(prepared["inputs"]), 60 * 3 + 1)
        self.assertEqual(count_large, count_small)
        with self.assertQueryCount(0):
            for _ in range(50):
                prepared["evaluate"](prepared["inputs"])
