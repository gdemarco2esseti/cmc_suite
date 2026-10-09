"""FCS-01 (extra T-B, dispatch C3) - rolling forecast splits semi-variable actuals with the cost function.

Rule: for a semi_variable actual of center x element in month m <= cutoff, the cost function of center x element
(fallback: the function of the center with element_id False) gives v and the driver; Q = ACTUAL facts
(version_id False) of that driver on the center in month m. variable = v x Q, fixed = actual - v x Q
(K of the function is NOT used: the actual absorbs the deviation from K). No function -> all fixed; a step
function -> all fixed; actual fixed / variable lines keep their behavior.

Hand-computed (cutoff 28 Feb 2033, positive figures):
  center S, energy, F1 semi_variable K 300 v 5 driver mh:
    Jan actual 1,000, Q mh 100 -> variable 500, fixed 500 (+ rent fixed actual 100 stays fixed: rent fixed 100)
    Feb actual 1,300, Q mh 200 -> variable 1,000, fixed 300
    distractors ignored: planned mh Jan 999 on the budget version, actual lh Jan 50 (other driver).
  center G: generic function (element False) v 2 driver lh; specific G x energy v 1 driver mh.
    labour Jan 800, Q lh 100 -> generic: variable 200, fixed 600
    energy Jan 400, Q mh 100 -> specific wins: variable 100, fixed 300 (generic would give 200 / 200)
  center N (no function): energy Jan 700 -> fixed 700, no variable line.
  center T (step function, steps 0-1,000 -> 500): energy Jan 900, Q mh 100 -> fixed 900.
  center Z (function v 5, no actual quantity in Jan): energy Jan 600 -> variable 0, fixed 600.
"""

from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetForecastSplit(CmcBudgetCase):
    """FCS-01: actual months of a rolling forecast are keyed fixed / variable like the budget."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Function = cls.env["cmc.cost.function"]
        cls.c_s = cls._center("BUD fs S")
        cls.c_g = cls._center("BUD fs G")
        cls.c_n = cls._center("BUD fs N")
        cls.c_t = cls._center("BUD fs T")
        cls.c_z = cls._center("BUD fs Z")
        cls.budget = cls._version(name="BUD fs 2033")
        cls.budget._cmc_budget_add({"center_id": cls.c_s.id, "element_id": cls.energy.id, "behavior": "fixed"}, 12000.0)

        cls._function(cls.c_s, cls.energy, cls.mh, fixed=300.0, rate=5.0)
        cls._cost(cls.c_s, 1000.0, d(1, 10), element=cls.energy, behavior="semi_variable")
        cls._cost(cls.c_s, 1300.0, d(2, 10), element=cls.energy, behavior="semi_variable")
        cls._cost(cls.c_s, 100.0, d(1, 12), element=cls.rent, behavior="fixed")
        cls._qty(cls.mh, cls.c_s, 1, 100.0)
        cls._qty(cls.mh, cls.c_s, 2, 200.0)
        # distractors: planned facts of the budget and actual facts of another driver are not Q_actual
        cls._qty(cls.mh, cls.c_s, 1, 999.0, version=cls.budget)
        cls._qty(cls.lh, cls.c_s, 1, 50.0)

        cls._function(cls.c_g, False, cls.lh, fixed=0.0, rate=2.0)
        cls._function(cls.c_g, cls.energy, cls.mh, fixed=0.0, rate=1.0)
        cls._cost(cls.c_g, 800.0, d(1, 10), element=cls.labour, behavior="semi_variable")
        cls._cost(cls.c_g, 400.0, d(1, 11), element=cls.energy, behavior="semi_variable")
        cls._qty(cls.lh, cls.c_g, 1, 100.0)
        cls._qty(cls.mh, cls.c_g, 1, 100.0)

        cls._cost(cls.c_n, 700.0, d(1, 10), element=cls.energy, behavior="semi_variable")
        cls._qty(cls.mh, cls.c_n, 1, 100.0)

        step = cls._function(cls.c_t, cls.energy, cls.mh, behavior="step")
        cls.env["cmc.cost.function.step"].create({"function_id": step.id, "qty_from": 0.0, "qty_to": 1000.0, "amount": 500.0})
        cls._cost(cls.c_t, 900.0, d(1, 10), element=cls.energy, behavior="semi_variable")
        cls._qty(cls.mh, cls.c_t, 1, 100.0)

        cls._function(cls.c_z, cls.energy, cls.mh, fixed=300.0, rate=5.0)
        cls._cost(cls.c_z, 600.0, d(1, 10), element=cls.energy, behavior="semi_variable")

    @classmethod
    def _function(cls, center, element, driver, fixed=0.0, rate=0.0, behavior="semi_variable"):
        return cls.Function.create({
            "name": f"BUD fn {center.name} {element.code if element else '*'}", "company_id": cls.company.id,
            "center_id": center.id, "element_id": element.id if element else False, "driver_id": driver.id,
            "behavior": behavior, "fixed_amount": fixed, "variable_rate": rate,
        })

    def _forecast(self, budget=None):
        return (budget or self.budget).with_user(self.manager)._cmc_rolling_forecast(d(2, 28)).sudo()

    def _actual_months(self, forecast, center):
        """{(month, element, behavior): summed figure} of the actual months (<= cutoff) of a center."""
        result = {}
        for line in forecast.budget_line_ids.filtered(lambda ln: ln.center_id == center and ln.date <= d(2)):
            key = (line.date.month, line.element_id, line.behavior)
            result[key] = round(result.get(key, 0.0) + line.figure, 2)
        return result

    def test_semi_variable_actual_split_with_center_element_function(self):
        # T-B / FCS-01 (C3): variable = v x Q_actual of the month, fixed = actual - v x Q_actual
        self.assertEqual(self._actual_months(self._forecast(), self.c_s), {
            (1, self.energy, "fixed"): 500.0, (1, self.energy, "variable"): 500.0,
            (2, self.energy, "fixed"): 300.0, (2, self.energy, "variable"): 1000.0,
            (1, self.rent, "fixed"): 100.0,
        })

    def test_split_keeps_month_total_equal_to_actual(self):
        # T-B / FCS-01: the split re-keys the actual, it never changes the forecast total
        forecast = self._forecast()
        actual_s = forecast.budget_line_ids.filtered(lambda ln: ln.center_id == self.c_s and ln.date <= d(2))
        self.assertAlmostEqual(sum(actual_s.mapped("figure")), 1000.0 + 1300.0 + 100.0)
        self.assertTrue(all(line.amount < 0 for line in actual_s), "split parts keep the natural cost sign")

    def test_center_level_function_is_the_fallback(self):
        # T-B / FCS-01 (C3): element without own function -> function of the center with element_id False;
        # an element-specific function wins over it
        self.assertEqual(self._actual_months(self._forecast(), self.c_g), {
            (1, self.labour, "fixed"): 600.0, (1, self.labour, "variable"): 200.0,
            (1, self.energy, "fixed"): 300.0, (1, self.energy, "variable"): 100.0,
        })

    def test_no_function_means_all_fixed(self):
        # T-B / FCS-01 (C3): without a cost function the semi-variable actual is planned as fixed
        self.assertEqual(self._actual_months(self._forecast(), self.c_n), {(1, self.energy, "fixed"): 700.0})

    def test_step_function_means_all_fixed(self):
        # T-B / FCS-01 (C3): a step cost has no rate per unit, the whole actual is fixed
        self.assertEqual(self._actual_months(self._forecast(), self.c_t), {(1, self.energy, "fixed"): 900.0})

    def test_no_actual_quantity_means_all_fixed(self):
        # T-B / FCS-01 (C3): v x 0 = 0, so the whole actual stays fixed (no zero-variable line required)
        got = self._actual_months(self._forecast(), self.c_z)
        self.assertEqual({k: v for k, v in got.items() if v}, {(1, self.energy, "fixed"): 600.0})

    def test_remainder_months_unchanged_by_split(self):
        # T-B / FCS-01: months after the cutoff are still the copy of the budget lines
        forecast = self._forecast()
        later = forecast.budget_line_ids.filtered(lambda ln: ln.center_id == self.c_s and ln.date > d(2))
        self.assertEqual((len(later), set(later.mapped("figure")), set(later.mapped("behavior"))), (10, {1000.0}, {"fixed"}))

    def test_split_query_count_independent_of_volume(self):
        # T-B / C3 / AD-114: one facts read and one function search per forecast, not per center x month
        def build(prefix, centers, months):
            for i in range(centers):
                center = self._center(f"BUD fs qc {prefix} {i}")
                self._function(center, self.energy, self.mh, rate=1.0)
                for month in range(1, months + 1):
                    self._cost(center, 100.0 + i, d(month, 10), element=self.energy, behavior="semi_variable")
                    self._qty(self.mh, center, month, 10.0 + i)

        counts = []
        for prefix, centers, months in (("small", 1, 1), ("large", 10, 2)):
            build(prefix, centers, months)
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            self._forecast()
            counts.append(self.cr.sql_log_count - before)
        self.assertLessEqual(counts[1], counts[0] + 4, f"query count of the forecast grows with the volume: {counts}")
