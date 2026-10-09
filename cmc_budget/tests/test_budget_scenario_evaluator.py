"""WP-11 real scenario evaluator ``budget_lines`` (AD-102): the budget lines of a version are its scenario inputs.

prepare(version) -> inputs keyed ``"<center id>/<element id>/<YYYY-MM>/<kind>"`` = sum of the positive figures;
evaluate(values) -> {(center id, element id, month): signed amount} (revenue +, cost -), pure (no DB access).
"""

from .common import CmcBudgetCase, d

KEY = "budget_lines"


class TestBudgetScenarioEvaluator(CmcBudgetCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.center_a = cls._center("BUD SCN A")
        cls.center_b = cls._center("BUD SCN B")
        cls.version = cls._version("BUD SCN 2033")
        V, A, B = cls.version, cls.center_a, cls.center_b
        cls._line(V, A, 1, 1000.0, element=cls.rent)
        cls._line(V, A, 1, 200.0, element=cls.rent, behavior="variable")  # same cell, other behavior: summed
        cls._line(V, A, 2, 500.0, element=cls.rent)
        cls._line(V, B, 1, 300.0, element=cls.energy, behavior="variable")
        cls._line(V, A, 1, 5000.0, element=cls.labour, kind="revenue")
        cls._line(V, False, 3, 700.0, element=cls.labour, kind="revenue")  # revenue without center

    def _prepare(self, version=None):
        version = version or self.version
        return version._cmc_scenario_evaluator(KEY)["prepare"](version)

    def _path(self, center, element, month, kind="cost"):
        return "%s/%s/%s/%s" % (center.id if center else False, element.id, d(month).strftime("%Y-%m"), kind)

    def test_registered_in_hook(self):
        # AD-102: cmc_budget registers its evaluator in the cmc_base hook
        keys = [entry["key"] for entry in self.version._cmc_scenario_evaluators()]
        self.assertIn(KEY, keys)

    def test_prepare_inputs_keyed_per_center_element_month_kind(self):
        # inputs = positive figures summed per center / element / month / kind
        inputs = self._prepare()["inputs"]
        A, B = self.center_a, self.center_b
        self.assertEqual(inputs, {
            self._path(A, self.rent, 1): 1200.0,
            self._path(A, self.rent, 2): 500.0,
            self._path(B, self.energy, 1): 300.0,
            self._path(A, self.labour, 1, "revenue"): 5000.0,
            self._path(False, self.labour, 3, "revenue"): 700.0,
        })

    def test_evaluate_base_reproduces_budget_lines(self):
        # base inputs give back the version's own signed amounts: revenue +, cost -
        prepared = self._prepare()
        out = prepared["evaluate"](prepared["inputs"])
        A, B = self.center_a, self.center_b
        self.assertEqual(out, {
            (A.id, self.rent.id, d(1)): -1200.0,
            (A.id, self.rent.id, d(2)): -500.0,
            (B.id, self.energy.id, d(1)): -300.0,
            (A.id, self.labour.id, d(1)): 5000.0,
            (False, self.labour.id, d(3)): 700.0,
        })
        # and equals the stored lines, grouped the same way
        expected = {}
        for line in self.version.budget_line_ids:
            key = (line.center_id.id or False, line.element_id.id or False, line.date.replace(day=1))
            expected[key] = expected.get(key, 0.0) + line.amount
        self.assertEqual(out, expected)
        self.assertAlmostEqual(sum(out.values()), sum(self.version.budget_line_ids.mapped("amount")))

    def test_changed_cost_input_lowers_its_cell(self):
        # a cost figure +100 -> that cell -100, every other cell unchanged
        prepared = self._prepare()
        base = prepared["evaluate"](prepared["inputs"])
        path = self._path(self.center_a, self.rent, 1)
        out = prepared["evaluate"]({**prepared["inputs"], path: prepared["inputs"][path] + 100.0})
        key = (self.center_a.id, self.rent.id, d(1))
        self.assertAlmostEqual(out[key] - base[key], -100.0)
        self.assertEqual({k: v for k, v in out.items() if k != key}, {k: v for k, v in base.items() if k != key})

    def test_changed_revenue_input_raises_its_cell(self):
        # a revenue figure +250 -> profit +250
        prepared = self._prepare()
        base = prepared["evaluate"](prepared["inputs"])
        path = self._path(self.center_a, self.labour, 1, "revenue")
        out = prepared["evaluate"]({**prepared["inputs"], path: 5250.0})
        self.assertAlmostEqual(sum(out.values()) - sum(base.values()), 250.0)

    def test_evaluate_is_pure(self):
        # AD-114: trials never hit the DB
        prepared = self._prepare()
        self.env.flush_all()
        with self.assertQueryCount(0):
            for _ in range(50):
                prepared["evaluate"](prepared["inputs"])

    def test_empty_version(self):
        # no budget line -> no input, nothing evaluated
        prepared = self._prepare(self._version("BUD SCN empty"))
        self.assertEqual(prepared["inputs"], {})
        self.assertEqual(prepared["evaluate"]({}), {})

    def test_prepare_query_count_independent_of_lines(self):
        # cost of access: prepare is one grouped read, not one query per line
        small = self._version("BUD SCN small")
        self._line(small, self.center_a, 1, 10.0)
        large = self._version("BUD SCN large")
        centers = [self._center("BUD SCN vol %s" % i) for i in range(10)]
        self.Line.create([
            {"version_id": large.id, "center_id": center.id, "element_id": element.id, "date": d(month), "figure": 10.0}
            for center in centers for element in (self.rent, self.energy) for month in range(1, 13)
        ])
        counts = []
        for version in (small, large):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            prepared = self._prepare(version)
            counts.append(self.cr.sql_log_count - before)
        self.assertEqual(len(prepared["inputs"]), 240)
        self.assertEqual(counts[0], counts[1])
