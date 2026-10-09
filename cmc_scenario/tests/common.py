"""WP-11 ``cmc_scenario`` - scenarios, Monte Carlo / LHS, sensitivity, version consolidation (FCS-02, RSK-01,
RSK-02, INT-04).

Sources: gap register rows FCS-02 (copy a baseline, change parameters, side-by-side delta P&L, delta = scenario -
baseline), RSK-01 (Monte Carlo / Latin Hypercube on uncertain inputs, normal / triangular / uniform, statistics,
percentiles, P(profit > 0); N >= 6 x variables), RSK-02 (rank correlation, tornado at +/-10%, trust abs(rho) >=
0.05, dominant > 0.10), INT-04 (assumption register, child -> parent consolidation); Emblemsvag LCC Ch3, Ch6-9,
App A (LHS, error ~ 1/sqrt(N), tornado one-at-a-time ignores interactions, rerun after removing the dominant
variables to expose masked ones).
Architecture: AD-101 (new module), AD-102 (depends ``cmc_base`` ONLY: the P&L of a version is computed by the
evaluators producers register in ``cmc_base``, see ``cmc_base/tests/test_wp11_stats.py``), AD-104 (version, copy,
assumptions in base; consolidation here), AD-113 (every engine is a run), AD-114 (no sample stored, N <= 10,000,
fixed seed; trials never hit the DB), AD-115 (security), AD-118 (WP-11 must; skip: stored samples, correlated
input sampling), AD-119 (``cmc_utils.draw/describe/histogram/spearman``).

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Module ``cmc_scenario``, depends ``["cmc_base"]`` only. Every model has ``company_id`` (runs: required, default
env.company, ``_check_company_auto = True``, versions ``check_company=True``; lines/results: related to the run,
stored) and a global ``ir.rule`` ``['|', ('company_id', '=', False), ('company_id', 'parent_of', company_ids)]``
outside noupdate. Runs inherit ``cmc.run.mixin`` (compute from draft, locked when done, reset deletes results
and clears header outputs to 0); input lines inherit ``cmc.run.line.mixin`` (ondelete cascade); result rows
inherit ``cmc.run.result.mixin``. Header outputs are written by Compute only (user write -> UserError).
Evaluator (every run has ``evaluator_key`` Char required): ``cmc.plan.version._cmc_scenario_evaluator(key)``;
``prepare(version)`` is called ONCE per version per Compute; ``evaluate(values)`` is pure, its amounts are in the
version currency and converted to company currency with ``version.currency_rate`` (line amounts stored through
``version._cmc_to_company_currency``). Profit of a version = sum of its evaluated amounts.

ACL: runs (``cmc.scenario.comparison``, ``cmc.scenario.simulation``, ``cmc.scenario.consolidation``):
group_cmc_user read/write/create (NO unlink), group_cmc_manager full; ``cmc.scenario.variable``: user and
manager full; results: user READ only, manager full; internal users without a CMC group: nothing.

FCS-02 scenario copy (``cmc.plan.version`` extended here)
- ``_cmc_scenario_copy()`` -> new DRAFT version = ``copy()`` (base: parent_id, assumptions, planned quantities,
  planned rates) with ``version_type = "simulation"``; ``action_cmc_create_scenario()`` -> window action
  (``res_model`` cmc.plan.version, ``res_id`` the copy). Allowed to group_cmc_user, also from an approved version.

FCS-02 ``cmc.scenario.comparison`` - side-by-side delta P&L
- Inputs: ``name``, ``company_id``, ``scenario_id`` (required), ``baseline_id`` (stored compute =
  ``scenario_id.parent_id``, readonly=False: may be chosen), ``evaluator_key``. No baseline at Compute -> UserError.
- Compute: both versions evaluated at their own inputs; one result line per key (center, element, month) present
  in either version (the missing side counts 0), created in ONE batch.
- ``line_ids`` -> ``cmc.scenario.comparison.line``: ``comparison_id``, ``center_id``, ``element_id``, ``date``,
  ``baseline_amount``, ``scenario_amount``, ``delta`` = scenario - baseline (Monetary, company currency),
  ``delta_pct`` = delta / abs(baseline_amount) x 100 (digits (16, 2); 0 when the baseline is 0). Positive delta =
  favourable (AD-112: a cost increase is a negative delta).
- Header outputs: ``baseline_profit``, ``scenario_profit``, ``delta_profit``.

RSK-01 / RSK-02 ``cmc.scenario.simulation`` - Monte Carlo on the inputs of one version
- Inputs: ``name``, ``company_id``, ``version_id`` (required), ``evaluator_key``, ``trials`` Integer (default
  1000; 1..10,000 else ValidationError), ``seed`` Integer (default 1), ``sampling`` ``lhs`` (default) / ``srs``,
  ``swing_pct`` (default 10.0, > 0 else ValidationError), ``significance_threshold`` (default 0.05),
  ``dominant_threshold`` (default 0.10), ``bin_count`` Integer (default 10; 1..100 else ValidationError),
  ``variable_ids``, ``parent_simulation_id`` (readonly: the run it was re-run from).
- ``cmc.scenario.variable`` (input line, ``_cmc_run_field = "simulation_id"``): ``simulation_id``, ``sequence``,
  ``name``, ``input_path`` Char required (a key of the evaluator inputs), ``distribution`` ``normal`` /
  ``triangular`` / ``uniform`` required, ``mean``, ``stdev``, ``low``, ``mode``, ``high``. ValidationError: normal
  stdev < 0; triangular not (low <= mode <= high and low < high); uniform low >= high; the same ``input_path``
  twice in one simulation.
- Compute (UserError): no variable; an ``input_path`` unknown to the evaluator (message names the path); trials
  < 6 x number of variables (RSK-01). Engine: ``prepare`` once; ``rng = random.Random(seed)``; per variable in
  (sequence, id) order ``cmc_utils.draw(distribution, params, trials, rng, lhs=sampling == "lhs")``; trial output =
  profit of the evaluator at the base inputs overridden by the sampled values, x currency_rate. Same inputs and
  seed -> identical outputs.
- Header outputs (Float digits (16, 2)): ``base_output`` (profit at the version inputs), ``mean_output``,
  ``stdev_output``, ``min_output``, ``max_output``, ``p5_output``, ``p50_output``, ``p95_output``,
  ``prob_positive_pct`` (share of trials with output > 0, percent) - from ``cmc_utils.describe``.
- ``bin_ids`` -> ``cmc.scenario.bin``: ``sequence`` (1..k), ``lower``, ``upper``, ``count``, ``frequency_pct``
  (16, 2) from ``cmc_utils.histogram(outputs, bin_count)``; sum of counts = trials.
- ``tornado_ids`` -> ``cmc.scenario.tornado`` (RSK-02, deterministic one-at-a-time from the version inputs, not
  from the distributions): ``variable_id``, ``input_path``, ``base_value``, ``output_low`` (that input x (1 -
  swing_pct/100), every other at base), ``output_high`` (x (1 + swing_pct/100)), ``swing`` = abs(high - low),
  ``rank`` (1 = widest swing).
- ``sensitivity_ids`` -> ``cmc.scenario.sensitivity``: ``variable_id``, ``input_path``, ``rank_correlation``
  (16, 4) = ``cmc_utils.spearman(samples of the variable, outputs)``, ``is_significant`` = abs(rho) >=
  significance_threshold, ``is_dominant`` = abs(rho) > dominant_threshold, ``rank`` (1 = largest abs(rho)).
- AD-114: no per-trial row anywhere (rows = bins + 2 x variables); the Compute query count does not depend on
  ``trials`` nor on the number of variables.
- ``_cmc_rerun_without_dominant()`` -> new DRAFT simulation with the same header inputs and the variables that
  were NOT dominant (dominant inputs stay at their base value), ``parent_simulation_id`` = self. UserError when
  self is not computed, has no dominant variable, or only dominant ones. ``action_rerun_without_dominant()`` ->
  window action (``res_model`` cmc.scenario.simulation, ``res_id`` the new run).

INT-04 ``cmc.scenario.consolidation`` - child versions into a parent
- Inputs: ``name``, ``company_id``, ``version_id`` (the parent, required), ``child_version_ids`` M2m (same
  company: check_company), ``evaluator_key``. ValidationError: the parent among its children.
- Compute (UserError): no child; a child not approved (message names it). Each child evaluated once; one line
  per (center, element, month) = sum over children of the line amount in company currency (each child line via
  ``child._cmc_to_company_currency``), created in one batch; ``total_amount`` = sum of lines. AD-002: the
  children are recorded with ``_cmc_record_usage``: a child cannot be reset while the consolidation is computed.
- ``line_ids`` -> ``cmc.scenario.consolidation.line``: ``consolidation_id``, ``center_id``, ``element_id``,
  ``date``, ``amount`` (Monetary, company currency).

Fixture (company A = env company; version span 2026; planned quantities of 4 test measures per center x month)
  Test evaluator ``wp11_test_cvr`` (registered by patching the cmc_base hook in setUp): per (center, month)
  revenue = price x volume, variable cost = -unit variable cost x volume, fixed cost = -fixed; input path
  ``"<center id>/<YYYY-MM>/<measure code>"``.
  Baseline (approved): center A price 10, volume 1,000, var 6, fixed 3,000 -> 10,000 / -6,000 / -3,000 = 1,000;
  center B price 20, volume 100, var 12, fixed 500 -> 2,000 / -1,200 / -500 = 300. Profit 1,300.
"""

from datetime import date

from odoo.tests.common import TransactionCase, new_test_user

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
EVALUATOR_KEY = "wp11_test_cvr"
PRICE, VOLUME, VARCOST, FIXED = "WP11PRICE", "WP11VOL", "WP11VAR", "WP11FIX"


class ScenarioCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        Driver = cls.env["cmc.cost.driver"]
        cls.drivers = {
            code: Driver.create({"name": "WP11 %s" % code, "code": code, "company_id": False, "measure_kind": "statistical"})
            for code in (PRICE, VOLUME, VARCOST, FIXED)
        }
        Element = cls.env["cmc.cost.element"]
        cls.el_revenue = Element.create({"name": "WP11 revenue", "code": "WP11REV", "company_id": False})
        cls.el_variable = Element.create({"name": "WP11 variable cost", "code": "WP11VC", "company_id": False})
        cls.el_fixed = Element.create({"name": "WP11 fixed cost", "code": "WP11FC", "company_id": False})
        cls.center_a = cls._center("WP11 center A")
        cls.center_b = cls._center("WP11 center B")
        cls.user = new_test_user(cls.env, login="cmc_wp11_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_wp11_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(cls.env, login="cmc_wp11_outsider", groups="base.group_user")
        cls.baseline = cls._version("WP11 baseline", {
            cls.center_a: (10.0, 1000.0, 6.0, 3000.0),
            cls.center_b: (20.0, 100.0, 12.0, 500.0),
        })
        cls.baseline.action_approve()

    def setUp(self):
        super().setUp()
        self.prepare_calls = []
        Version = type(self.env["cmc.plan.version"])
        # the cmc_base hook must exist (AD-102): patching a missing attribute fails here, which is the point
        original = Version._cmc_scenario_evaluators
        entry = {"key": EVALUATOR_KEY, "name": "WP11 test CVR", "prepare": self._prepare}

        def evaluators(model):
            return original(model) + [entry]

        self.patch(Version, "_cmc_scenario_evaluators", evaluators)

    # ---- fixture helpers ---------------------------------------------------

    @classmethod
    def _center(cls, name, company=None):
        vals = {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final"}
        if company:
            vals["company_id"] = company.id
        return cls.env["account.analytic.account"].create(vals)

    @classmethod
    def _version(cls, name, cells, month=JAN, company=None, **kw):
        """cells = {center: (price, volume, unit variable cost, fixed)} planned in `month`."""
        version = cls.env["cmc.plan.version"].create({
            "name": name, "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), "version_type": "budget",
            "company_id": (company or cls.company).id, **kw,
        })
        cls._facts(version, cells, month)
        return version

    @classmethod
    def _facts(cls, version, cells, month=JAN):
        cls.env["cmc.measure.quantity"].create([
            {"driver_id": cls.drivers[code].id, "center_id": center.id, "date": month, "quantity": qty,
             "version_id": version.id, "company_id": version.company_id.id}
            for center, values in cells.items()
            for code, qty in zip((PRICE, VOLUME, VARCOST, FIXED), values, strict=True)
        ])

    @staticmethod
    def _path(center, code, month=JAN):
        return "%s/%s/%s" % (center.id, month.strftime("%Y-%m"), code)

    def _set_input(self, version, center, code, value, month=JAN):
        # as the version's user: a CMC user edits the inputs of a draft scenario
        fact = version.env["cmc.measure.quantity"].search([
            ("version_id", "=", version.id), ("center_id", "=", center.id),
            ("driver_id", "=", self.drivers[code].id), ("date", "=", month),
        ])
        fact.ensure_one()
        fact.quantity = value

    # ---- the test-only evaluator (a producer would register it in its own module) -----

    def _prepare(self, version):
        self.prepare_calls.append(version.id)
        code_by_driver = {driver.id: code for code, driver in self.drivers.items()}
        facts = version.env["cmc.measure.quantity"].search(
            [("version_id", "=", version.id), ("driver_id", "in", list(code_by_driver))]
        )
        inputs, cells = {}, set()
        for fact in facts:
            cell = (fact.center_id.id, fact.date)
            cells.add(cell)
            inputs["%s/%s/%s" % (cell[0], cell[1].strftime("%Y-%m"), code_by_driver[fact.driver_id.id])] = fact.quantity
        rev, var, fix = self.el_revenue.id, self.el_variable.id, self.el_fixed.id

        def evaluate(values):
            out = {}
            for center_id, month in cells:
                prefix = "%s/%s/" % (center_id, month.strftime("%Y-%m"))
                price = values.get(prefix + PRICE, 0.0)
                volume = values.get(prefix + VOLUME, 0.0)
                out[(center_id, rev, month)] = price * volume
                out[(center_id, var, month)] = -values.get(prefix + VARCOST, 0.0) * volume
                out[(center_id, fix, month)] = -values.get(prefix + FIXED, 0.0)
            return out

        return {"inputs": inputs, "evaluate": evaluate}

    # ---- run builders ----------------------------------------------------------

    def _scenario(self, user=None):
        version = self.baseline.with_user(user) if user else self.baseline
        return version._cmc_scenario_copy()

    def _comparison(self, scenario, user=None, **kw):
        model = self.env["cmc.scenario.comparison"]
        vals = {"name": "WP11 comparison", "scenario_id": scenario.id, "evaluator_key": EVALUATOR_KEY, **kw}
        return (model.with_user(user) if user else model).create(vals)

    def _simulation(self, variables, user=None, version=None, **kw):
        """variables = [(input path, distribution, {params})]"""
        model = self.env["cmc.scenario.simulation"]
        vals = {
            "name": "WP11 simulation", "version_id": (version or self.baseline).id, "evaluator_key": EVALUATOR_KEY,
            "trials": 2000, "seed": 42, "sampling": "lhs",
            "variable_ids": [
                (0, 0, {"sequence": i, "name": path, "input_path": path, "distribution": dist, **params})
                for i, (path, dist, params) in enumerate(variables)
            ],
            **kw,
        }
        return (model.with_user(user) if user else model).create(vals)

    def _consolidation(self, parent, children, user=None, **kw):
        model = self.env["cmc.scenario.consolidation"]
        vals = {"name": "WP11 consolidation", "version_id": parent.id, "child_version_ids": [(6, 0, children.ids)],
                "evaluator_key": EVALUATOR_KEY, **kw}
        return (model.with_user(user) if user else model).create(vals)

    @staticmethod
    def _by_key(lines):
        return {(line.center_id, line.element_id, line.date): line for line in lines}
