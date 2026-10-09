"""WP-01 / CLS-02 (+ AD-119) - cost functions per center x element: K, v, step table, relevant range.

CONTRACT
- Pure helpers in ``odoo.addons.cmc_base.models.cmc_utils`` (stdlib only, AD-119):
  - ``high_low(points)``: ``points`` = iterable of ``(quantity, cost)``. Uses the points with the
    LOWEST and HIGHEST quantity (not cost): ``v = (C_qmax - C_qmin) / (Qmax - Qmin)``,
    ``K = C_qmin - v * Qmin``. Returns ``(K, v)``. ``ValueError`` with < 2 points or Qmax == Qmin.
  - ``ols_fit(points)``: least squares ``C = K + v Q`` (``v = Sxy / Sxx``, ``K = mean_C - v mean_Q``),
    returns ``(K, v, r2)`` with ``r2 = Sxy^2 / (Sxx * Syy)`` (1.0 when Syy == 0). ``ValueError`` with
    < 2 points or all quantities equal.
- Model ``cmc.cost.function`` (company rule ``parent_of``, ``_check_company_auto``; ACL user read only,
  manager full): ``name``, ``company_id`` (required, default env.company), ``center_id`` (M2o
  account.analytic.account, required, check_company), ``element_id`` (M2o cmc.cost.element, optional,
  check_company), ``driver_id`` (M2o cmc.cost.driver, required, check_company), ``behavior`` Selection
  ``fixed`` / ``variable`` / ``semi_variable`` (default) / ``step``, ``fixed_amount`` (K, per month),
  ``variable_rate`` (v, per driver unit), ``range_min`` / ``range_max`` (0 = no bound, max >= min else
  ValidationError), ``method`` Selection ``manual`` (default) / ``high_low`` / ``ols``, ``r2`` Float
  (readonly, set by OLS), ``date_from`` / ``date_to`` (history span for the estimate),
  ``step_ids`` O2m ``cmc.cost.function.step`` (``function_id``, ``qty_from``, ``qty_to``, ``amount``;
  a step covers ``qty_from <= Q < qty_to``; overlapping steps -> ValidationError).
- ``_cmc_evaluate(quantity)`` -> cost: fixed K; variable v*Q; semi_variable K + v*Q; step = amount of the
  step covering Q (UserError when no step covers Q).
- ``_cmc_range_warning(quantity)`` -> translated str when Q is outside [range_min, range_max] (each bound
  only when non-zero), else False. Used by CVR derive and WP-02.
- ``action_estimate()``: monthly history over [date_from, date_to]: cost per month = cost base of the
  center (only ``element_id`` when set) read grouped by ``date:month`` (``_cmc_cost_read_group``),
  quantity per month = ACTUAL facts (``version_id`` empty) of ``driver_id`` with ``center_id`` = center,
  company of the function. Only months having BOTH a cost and a quantity are used. ``high_low`` /
  ``ols`` write ``fixed_amount``, ``variable_rate`` (and ``r2`` for ols). UserError when ``method`` is
  ``manual`` or fewer than 2 usable months. Requires group_cmc_manager (AccessError otherwise).

Hand-computed history (2031-01..05): Q = 100,200,300,400,500; C = 1000,1700,2000,2300,3000.
  high-low: v = (3000-1000)/(500-100) = 5, K = 1000 - 5*100 = 500.
  OLS: mean Q 300, mean C 2000, Sxy = 460000, Sxx = 100000 -> v = 4.6, K = 2000 - 4.6*300 = 620,
  r2 = 460000^2 / (100000 * 2180000) = 0.970642.
"""

from datetime import date

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models import cmc_utils

HISTORY = [(1, 100.0, 1000.0), (2, 200.0, 1700.0), (3, 300.0, 2000.0), (4, 400.0, 2300.0), (5, 500.0, 3000.0)]


@tagged("post_install", "-at_install")
class TestCostFunctionHelpers(TransactionCase):
    """AD-119: the estimators are pure stdlib functions shared by every WP."""

    def test_high_low_uses_extreme_quantities(self):
        # CLS-02: v = (Cmax-Cmin)/(Qmax-Qmin), K = Cmin - v*Qmin, extremes chosen on Q
        k, v = cmc_utils.high_low([(300.0, 2000.0), (100.0, 1000.0), (500.0, 3000.0), (200.0, 1700.0), (400.0, 2300.0)])
        self.assertAlmostEqual(v, 5.0)
        self.assertAlmostEqual(k, 500.0)

    def test_ols_fit_least_squares(self):
        # CLS-02: least squares Sy = nb + aSx, Sxy = bSx + aSx2
        k, v, r2 = cmc_utils.ols_fit([(q, c) for _m, q, c in HISTORY])
        self.assertAlmostEqual(v, 4.6)
        self.assertAlmostEqual(k, 620.0)
        self.assertAlmostEqual(r2, 0.970642, places=5)

    def test_estimators_refuse_degenerate_history(self):
        # CLS-02: one point or a constant quantity cannot separate K from v
        for func in (cmc_utils.high_low, cmc_utils.ols_fit):
            with self.assertRaises(ValueError):
                func([(100.0, 1000.0)])
            with self.assertRaises(ValueError):
                func([(100.0, 1000.0), (100.0, 1200.0)])


@tagged("post_install", "-at_install")
class TestCostFunction(TransactionCase):
    """CLS-02: a semi-variable cost is split into K + vQ from history, with range warnings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Fn = cls.env["cmc.cost.function"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.AA.create(
            {"name": "WP01 CF Maintenance", "plan_id": cls.plan.id, "cmc_center_type": "auxiliary",
             "company_id": cls.company.id}
        )
        cls.element = cls.env["cmc.cost.element"].create({"name": "WP01 Energy", "code": "WP01CF_EN"})
        cls.other_element = cls.env["cmc.cost.element"].create({"name": "WP01 Other", "code": "WP01CF_OT"})
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "WP01 Machine hours", "code": "WP01CF_MH"})
        for month, qty, cost in HISTORY:
            cls.AL.create({"name": "hist", "account_id": cls.center.id, "amount": -cost,
                           "date": date(2031, month, 10), "cmc_cost_element_id": cls.element.id})
            cls.env["cmc.measure.quantity"].create({"driver_id": cls.driver.id, "date": date(2031, month, 1),
                                                    "center_id": cls.center.id, "quantity": qty})
        # noise the estimator must ignore: another element in March, a month with cost and no quantity
        cls.AL.create({"name": "noise", "account_id": cls.center.id, "amount": -777.0,
                       "date": date(2031, 3, 12), "cmc_cost_element_id": cls.other_element.id})
        cls.AL.create({"name": "no qty", "account_id": cls.center.id, "amount": -9999.0,
                       "date": date(2031, 6, 12), "cmc_cost_element_id": cls.element.id})
        cls.manager = new_test_user(cls.env, login="wp01_cf_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.user = new_test_user(cls.env, login="wp01_cf_user", groups="base.group_user,cmc_base.group_cmc_user")

    def _function(self, **kw):
        vals = {"name": "Energy maintenance", "center_id": self.center.id, "element_id": self.element.id,
                "driver_id": self.driver.id, "behavior": "semi_variable",
                "date_from": date(2031, 1, 1), "date_to": date(2031, 6, 30)}
        vals.update(kw)
        return self.Fn.create(vals)

    def test_estimate_high_low_from_history(self):
        # CLS-02: K and v from the monthly history of the center x element vs driver facts
        fn = self._function(method="high_low")
        fn.with_user(self.manager).action_estimate()
        self.assertAlmostEqual(fn.variable_rate, 5.0)
        self.assertAlmostEqual(fn.fixed_amount, 500.0)

    def test_estimate_ols_from_history(self):
        # CLS-02: OLS fit, with r2 stored; June (cost without quantity) and other elements are ignored
        fn = self._function(method="ols")
        fn.with_user(self.manager).action_estimate()
        self.assertAlmostEqual(fn.variable_rate, 4.6)
        self.assertAlmostEqual(fn.fixed_amount, 620.0)
        self.assertAlmostEqual(fn.r2, 0.970642, places=5)

    def test_estimate_needs_two_months(self):
        # CLS-02: one usable month cannot be split -> fail loud
        fn = self._function(method="ols", date_from=date(2031, 1, 1), date_to=date(2031, 1, 31))
        with self.assertRaises(UserError):
            fn.action_estimate()

    def test_estimate_manual_method_refused(self):
        # CLS-02: a manual function has no estimator
        fn = self._function(method="manual")
        with self.assertRaises(UserError):
            fn.action_estimate()

    def test_estimate_requires_manager(self):
        # AD-115: cost functions are configuration, a Controlling User only reads them
        fn = self._function(method="high_low")
        self.assertEqual(fn.with_user(self.user).read(["name"])[0]["name"], "Energy maintenance")
        with self.assertRaises(AccessError):
            fn.with_user(self.user).action_estimate()

    def test_evaluate_by_behavior(self):
        # CLS-02: C = K; C = vQ; C = K + vQ
        fn = self._function(fixed_amount=500.0, variable_rate=5.0)
        self.assertAlmostEqual(fn._cmc_evaluate(200.0), 1500.0)
        fn.behavior = "fixed"
        self.assertAlmostEqual(fn._cmc_evaluate(200.0), 500.0)
        fn.behavior = "variable"
        self.assertAlmostEqual(fn._cmc_evaluate(200.0), 1000.0)

    def test_evaluate_step_function(self):
        # CLS-02: step cost = amount of the step covering Q (qty_from <= Q < qty_to)
        fn = self._function(behavior="step", step_ids=[
            (0, 0, {"qty_from": 0.0, "qty_to": 100.0, "amount": 1000.0}),
            (0, 0, {"qty_from": 100.0, "qty_to": 200.0, "amount": 1800.0}),
            (0, 0, {"qty_from": 200.0, "qty_to": 300.0, "amount": 2500.0}),
        ])
        self.assertAlmostEqual(fn._cmc_evaluate(0.0), 1000.0)
        self.assertAlmostEqual(fn._cmc_evaluate(150.0), 1800.0)
        self.assertAlmostEqual(fn._cmc_evaluate(100.0), 1800.0)
        with self.assertRaises(UserError):
            fn._cmc_evaluate(350.0)

    def test_overlapping_steps_refused(self):
        # CLS-02: a quantity must fall in one step only
        with self.assertRaises(ValidationError):
            self._function(behavior="step", step_ids=[
                (0, 0, {"qty_from": 0.0, "qty_to": 150.0, "amount": 1000.0}),
                (0, 0, {"qty_from": 100.0, "qty_to": 200.0, "amount": 1800.0}),
            ])

    def test_range_warning(self):
        # CLS-02: warn when a computation leaves the relevant range
        fn = self._function(fixed_amount=500.0, variable_rate=5.0, range_min=150.0, range_max=450.0)
        self.assertFalse(fn._cmc_range_warning(300.0))
        self.assertTrue(fn._cmc_range_warning(600.0))
        self.assertTrue(fn._cmc_range_warning(100.0))
        unbounded = self._function(name="unbounded", fixed_amount=500.0, variable_rate=5.0)
        self.assertFalse(unbounded._cmc_range_warning(1e9))

    def test_range_bounds_ordered(self):
        # CLS-02: max below min is not a range
        with self.assertRaises(ValidationError):
            self._function(range_min=450.0, range_max=150.0)

    def test_center_of_other_company_refused(self):
        # AD-115: company check on the center
        company_b = self.env["res.company"].create({"name": "WP01 CF Co B"})
        foreign = self.AA.create({"name": "WP01 CF foreign", "plan_id": self.plan.id,
                                  "cmc_center_type": "final", "company_id": company_b.id})
        with self.assertRaises(UserError):
            self._function(center_id=foreign.id)
