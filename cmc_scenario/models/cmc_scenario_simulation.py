import random

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models import cmc_utils

MIN_TRIALS_PER_VARIABLE = 6
MAX_TRIALS = 10000
DISTRIBUTIONS = [("normal", "Normal"), ("triangular", "Triangular"), ("uniform", "Uniform")]


class CmcScenarioSimulation(models.Model):
    _name = "cmc.scenario.simulation"
    _inherit = ["cmc.scenario.run.mixin"]
    _description = "Monte Carlo Simulation"
    _order = "id desc"

    _cmc_input_fields = (
        "company_id", "evaluator_key", "version_id", "trials", "seed", "sampling", "swing_pct", "significance_threshold",
        "dominant_threshold", "bin_count", "variable_ids",
    )
    _cmc_result_fields = ("bin_ids", "tornado_ids", "sensitivity_ids")
    _cmc_output_fields = (
        "base_output", "mean_output", "stdev_output", "min_output", "max_output", "p5_output", "p50_output", "p95_output",
        "prob_positive_pct",
    )

    version_id = fields.Many2one("cmc.plan.version", required=True, check_company=True, index=True)
    trials = fields.Integer(default=1000)
    seed = fields.Integer(default=1)
    sampling = fields.Selection([("lhs", "Latin Hypercube"), ("srs", "Simple random")], required=True, default="lhs")
    swing_pct = fields.Float(string="Tornado Swing %", default=10.0)
    significance_threshold = fields.Float(default=0.05, digits=(16, 4))
    dominant_threshold = fields.Float(default=0.10, digits=(16, 4))
    bin_count = fields.Integer(default=10)
    parent_simulation_id = fields.Many2one("cmc.scenario.simulation", readonly=True, copy=False, index=True)
    variable_ids = fields.One2many("cmc.scenario.variable", "simulation_id", copy=True)
    bin_ids = fields.One2many("cmc.scenario.bin", "simulation_id", copy=False)
    tornado_ids = fields.One2many("cmc.scenario.tornado", "simulation_id", copy=False)
    sensitivity_ids = fields.One2many("cmc.scenario.sensitivity", "simulation_id", copy=False)
    base_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    mean_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    stdev_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    min_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    max_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    p5_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    p50_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    p95_output = fields.Float(digits=(16, 2), readonly=True, copy=False)
    prob_positive_pct = fields.Float(string="P(profit > 0) %", digits=(16, 2), readonly=True, copy=False)

    @api.constrains("trials", "bin_count", "swing_pct")
    def _check_bounds(self):
        for run in self:
            if not 1 <= run.trials <= MAX_TRIALS:
                raise ValidationError(self.env._("Trials must be between 1 and %s.", MAX_TRIALS))
            if not 1 <= run.bin_count <= 100:
                raise ValidationError(self.env._("The histogram needs between 1 and 100 bins."))
            if run.swing_pct <= 0:
                raise ValidationError(self.env._("The tornado swing must be positive."))

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        variables = self.variable_ids
        if not variables:
            raise UserError(self.env._("Add at least one uncertain input."))
        prepared = self._scenario_prepare(self.version_id)
        inputs, evaluate = prepared["inputs"], prepared["evaluate"]
        unknown = [variable.input_path for variable in variables if variable.input_path not in inputs]
        if unknown:
            raise UserError(self.env._("The evaluator does not read the input(s) %s.", ", ".join(unknown)))
        if self.trials < MIN_TRIALS_PER_VARIABLE * len(variables):
            raise UserError(self.env._("%(variables)s variable(s) need at least %(trials)s trials.", variables=len(variables), trials=MIN_TRIALS_PER_VARIABLE * len(variables)))
        rate = self.version_id.currency_rate

        def profit(values):
            return sum(evaluate(values).values()) * rate

        rng = random.Random(self.seed)
        paths = variables.mapped("input_path")
        samples = [
            cmc_utils.draw(variable.distribution, variable._params(), self.trials, rng, lhs=self.sampling == "lhs")
            for variable in variables
        ]
        values = dict(inputs)
        outputs = []
        for trial in range(self.trials):
            for path, sample in zip(paths, samples, strict=True):
                values[path] = sample[trial]
            outputs.append(profit(values))
        self._scenario_write_bins(outputs)
        self._scenario_write_tornado(variables, inputs, profit)
        self._scenario_write_sensitivity(variables, samples, outputs)
        stats = cmc_utils.describe(outputs)
        self.write({
            "base_output": profit(inputs), "mean_output": stats["mean"], "stdev_output": stats["stdev"], "min_output": stats["min"],
            "max_output": stats["max"], "p5_output": stats["p5"], "p50_output": stats["p50"], "p95_output": stats["p95"],
            "prob_positive_pct": stats["prob_positive"] * 100,
        })

    def _scenario_write_bins(self, outputs):
        bins = cmc_utils.histogram(outputs, self.bin_count)
        # why sudo: users read the results, the engine writes them
        self.env["cmc.scenario.bin"].sudo().create([
            {
                "simulation_id": self.id, "sequence": index, "lower": lower, "upper": upper, "count": count,
                "frequency_pct": count / len(outputs) * 100,
            }
            for index, (lower, upper, count) in enumerate(bins, start=1)
        ])

    def _scenario_write_tornado(self, variables, inputs, profit):
        swing = self.swing_pct / 100
        rows = []
        for variable in variables:
            base = inputs[variable.input_path]
            low = profit({**inputs, variable.input_path: base * (1 - swing)})
            high = profit({**inputs, variable.input_path: base * (1 + swing)})
            rows.append({
                "simulation_id": self.id, "variable_id": variable.id, "input_path": variable.input_path, "base_value": base,
                "output_low": low, "output_high": high, "swing": abs(high - low),
            })
        rows.sort(key=lambda row: -row["swing"])
        self.env["cmc.scenario.tornado"].sudo().create([{**row, "rank": rank} for rank, row in enumerate(rows, start=1)])

    def _scenario_write_sensitivity(self, variables, samples, outputs):
        rows = [
            {
                "simulation_id": self.id, "variable_id": variable.id, "input_path": variable.input_path,
                "rank_correlation": cmc_utils.spearman(sample, outputs),
            }
            for variable, sample in zip(variables, samples, strict=True)
        ]
        rows.sort(key=lambda row: -abs(row["rank_correlation"]))
        for rank, row in enumerate(rows, start=1):
            rho = abs(row["rank_correlation"])
            row.update(rank=rank, is_significant=rho >= self.significance_threshold, is_dominant=rho > self.dominant_threshold)
        self.env["cmc.scenario.sensitivity"].sudo().create(rows)

    # ---- re-run ---------------------------------------------------------

    def _cmc_rerun_without_dominant(self):
        self.ensure_one()
        if self.state != "done":
            raise UserError(self.env._("Compute the simulation before re-running it."))
        dominant = self.sensitivity_ids.filtered("is_dominant").variable_id
        if not dominant:
            raise UserError(self.env._("No input is dominant: there is nothing to remove."))
        keep = self.variable_ids - dominant
        if not keep:
            raise UserError(self.env._("Every input is dominant: nothing would be left to simulate."))
        return self.copy({
            "name": self.env._("%s (without dominant inputs)", self.name),
            "parent_simulation_id": self.id,
            "variable_ids": [Command.create({**vals, "simulation_id": False}) for vals in keep.copy_data()],
        })

    def action_rerun_without_dominant(self):
        rerun = self._cmc_rerun_without_dominant()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": rerun.id,
            "view_mode": "form",
            "target": "current",
        }


class CmcScenarioVariable(models.Model):
    _name = "cmc.scenario.variable"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Uncertain Input"
    _order = "sequence, id"
    _cmc_run_field = "simulation_id"

    simulation_id = fields.Many2one("cmc.scenario.simulation", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="simulation_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char()
    input_path = fields.Char(required=True, help="Key of one input read by the evaluator.")
    distribution = fields.Selection(DISTRIBUTIONS, required=True, default="normal")
    mean = fields.Float(digits=(16, 4))
    stdev = fields.Float(digits=(16, 4))
    low = fields.Float(digits=(16, 4))
    mode = fields.Float(digits=(16, 4))
    high = fields.Float(digits=(16, 4))

    def _params(self):
        self.ensure_one()
        keys = {"normal": ("mean", "stdev"), "triangular": ("low", "mode", "high"), "uniform": ("low", "high")}[self.distribution]
        return {key: self[key] for key in keys}

    @api.constrains("distribution", "mean", "stdev", "low", "mode", "high")
    def _check_distribution_params(self):
        for variable in self:
            try:
                cmc_utils.inverse_cdf(variable.distribution, variable._params(), 0.5)
            except ValueError as error:
                raise ValidationError(self.env._("%(path)s: %(error)s", path=variable.input_path, error=error)) from error

    @api.constrains("simulation_id", "input_path")
    def _check_unique_path(self):
        for simulation in self.simulation_id:
            paths = simulation.variable_ids.mapped("input_path")
            if len(paths) != len(set(paths)):
                raise ValidationError(self.env._("An input can have one distribution only in %s.", simulation.display_name))


class CmcScenarioBin(models.Model):
    _name = "cmc.scenario.bin"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Simulation Histogram Bin"
    _order = "simulation_id, sequence"

    simulation_id = fields.Many2one("cmc.scenario.simulation", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="simulation_id.company_id", store=True, index=True)
    sequence = fields.Integer()
    lower = fields.Float(digits=(16, 2))
    upper = fields.Float(digits=(16, 2))
    count = fields.Integer()
    frequency_pct = fields.Float(string="Frequency %", digits=(16, 2))


class CmcScenarioTornado(models.Model):
    _name = "cmc.scenario.tornado"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Simulation Tornado Row"
    _order = "simulation_id, rank"

    simulation_id = fields.Many2one("cmc.scenario.simulation", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="simulation_id.company_id", store=True, index=True)
    variable_id = fields.Many2one("cmc.scenario.variable", ondelete="cascade")
    input_path = fields.Char()
    base_value = fields.Float(digits=(16, 4))
    output_low = fields.Float(digits=(16, 2))
    output_high = fields.Float(digits=(16, 2))
    swing = fields.Float(digits=(16, 2))
    rank = fields.Integer()


class CmcScenarioSensitivity(models.Model):
    _name = "cmc.scenario.sensitivity"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Simulation Rank Correlation"
    _order = "simulation_id, rank"

    simulation_id = fields.Many2one("cmc.scenario.simulation", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="simulation_id.company_id", store=True, index=True)
    variable_id = fields.Many2one("cmc.scenario.variable", ondelete="cascade")
    input_path = fields.Char()
    rank_correlation = fields.Float(digits=(16, 4))
    is_significant = fields.Boolean()
    is_dominant = fields.Boolean()
    rank = fields.Integer()
