from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

from odoo.addons.cmc_base.models.cmc_utils import month_range, month_start, simple_payback

# shipped attributes whose value is frozen on the activity costs at compute (AD-106 inv.3)
ATTRIBUTE_SNAPSHOTS = {"VA_CLASS": "va_class_value_id", "COQ": "coq_value_id", "EFFICIENCY": "efficiency_value_id"}
COQ_BUCKETS = ("prevention", "appraisal", "internal_failure", "external_failure", "error_free", "unclassified")
PARETO_A, PARETO_B = 80.0, 95.0
PERCENT_TOLERANCE = 1e-6


class CmcMeasureQuantity(models.Model):
    _inherit = "cmc.measure.quantity"

    activity_id = fields.Many2one(
        "cmc.abc.activity", index=True, check_company=True, domain="[('node_type', '=', 'activity')]",
        help="Grain of an activity performance measure (volume, cycle time, defects).",
    )


class CmcAbcActivityMeasure(models.Model):
    _name = "cmc.abc.activity.measure"
    _description = "ABC Activity Performance Measure"
    _order = "activity_id, role"
    _check_company_auto = True

    activity_id = fields.Many2one(
        "cmc.abc.activity", required=True, ondelete="cascade", index=True, check_company=True,
        domain="[('node_type', '=', 'activity')]",
    )
    company_id = fields.Many2one(related="activity_id.company_id", store=True, index=True)
    measure_id = fields.Many2one(
        "cmc.cost.driver", required=True, check_company=True, ondelete="restrict",
        domain="[('measure_kind', '=', 'statistical')]",
    )
    role = fields.Selection(
        [("output", "Output volume"), ("cycle_time", "Cycle time"), ("quality", "Quality (defects)")], required=True,
    )
    target = fields.Float(help="Target of the measure per period.")

    @api.constrains("activity_id", "measure_id", "role")
    def _check_measure(self):
        for line in self:
            if line.activity_id.node_type != "activity":
                raise ValidationError(
                    self.env._("%s: measures are kept on activities, not on processes or tasks.", line.activity_id.display_name)
                )
            if line.measure_id.measure_kind != "statistical":
                raise ValidationError(self.env._("%s: an allocation driver is not a performance measure.", line.measure_id.display_name))
        groups = self._read_group([("activity_id", "in", self.activity_id.ids)], ["activity_id", "role"], ["__count"])
        for activity, role, count in groups:
            if count > 1:
                raise ValidationError(
                    self.env._("%(activity)s has more than one %(role)s measure.", activity=activity.display_name, role=role)
                )


class CmcAbcActivityPerformance(models.Model):
    _name = "cmc.abc.activity.performance"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Activity Performance"
    _order = "run_id, activity_id"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    activity_id = fields.Many2one("cmc.abc.activity", required=True, index=True)
    month = fields.Date(index=True, help="First day of the month of the run.")
    activity_cost = fields.Monetary()
    output_qty = fields.Float()
    cost_per_output = fields.Monetary()
    cycle_time = fields.Float(help="Cycle time measure per output unit.")
    defect_qty = fields.Float()
    quality_rate = fields.Float(help="Defects per 100 output units.")


class CmcAbcActivity(models.Model):
    _inherit = "cmc.abc.activity"

    def _cmc_performance_trend(self, date_from, date_to):
        """[{month, activity_cost, output_qty, cost_per_output, cycle_time, quality_rate}]: the latest done run of each month."""
        self.ensure_one()
        runs = self.env["cmc.abc.run"]._cmc_month_runs(date_from, date_to)
        rows = self.env["cmc.abc.activity.performance"].search(
            [("activity_id", "=", self.id), ("run_id", "in", [run.id for run in runs.values()])]
        )
        by_run = {row.run_id.id: row for row in rows}
        return [
            {
                "month": month, "activity_cost": by_run[run.id].activity_cost, "output_qty": by_run[run.id].output_qty,
                "cost_per_output": by_run[run.id].cost_per_output, "cycle_time": by_run[run.id].cycle_time,
                "quality_rate": by_run[run.id].quality_rate,
            }
            for month, run in sorted(runs.items()) if run.id in by_run
        ]


class CmcAbcRun(models.Model):
    _inherit = "cmc.abc.run"

    @api.constrains("defect_measure_id")
    def _check_defect_measure(self):
        if self.defect_measure_id.filtered(lambda driver: driver.measure_kind != "statistical"):
            raise ValidationError(self.env._("The defect measure must be a statistical measure."))

    def _cmc_check_no_dependents(self):
        """AD-002: initiatives and benchmarks read the results of their runs: reset/delete those first."""
        super()._cmc_check_no_dependents()
        if not self.ids:
            return
        # why sudo: the dependency must be seen whole whatever the user's rules; only names are reported
        initiatives = self.env["cmc.abm.initiative"].sudo().search(
            ["|", ("baseline_run_id", "in", self.ids), ("review_run_id", "in", self.ids)]
        )
        benchmarks = self.env["cmc.abm.benchmark"].sudo().search([("run_ids", "in", self.ids)])
        if initiatives or benchmarks:
            raise UserError(
                self.env._(
                    "These runs are read by other documents: %(names)s. Delete or change them first.",
                    names=", ".join(initiatives.mapped("display_name") + benchmarks.mapped("display_name")),
                )
            )

    def _cmc_span_months(self):
        return len(month_range(self.date_from, self.date_to)) if self.date_from and self.date_to else 1

    # ---- compute-time snapshots -----------------------------------------------

    def _cmc_abm_snapshot(self, rows, flows, results):
        """Freeze attribute values and Pareto ranks on the activity cost rows, NVA cost on the results."""
        rounding = self.company_id.currency_id.rounding
        activities = self.env["cmc.abc.activity"].browse([row["activity_id"] for row in rows])
        for code, field in ATTRIBUTE_SNAPSHOTS.items():
            value_of = {act: val for val, ids in activities._cmc_group_by_attribute(code).items() for act in ids}
            for row in rows:
                row[field] = value_of.get(row["activity_id"], False)
        code_of = dict(zip(activities.ids, activities.mapped("code")))
        total = sum(row["resource_cost"] for row in rows)
        cumulative = 0.0
        for rank, row in enumerate(sorted(rows, key=lambda r: (-r["resource_cost"], code_of[r["activity_id"]])), 1):
            cumulative += row["resource_cost"]
            share, cumulated = (row["resource_cost"] / total * 100.0, cumulative / total * 100.0) if total else (0.0, 100.0)
            row.update(
                pareto_rank=rank, cost_share=share, cumulative_share=cumulated,
                pareto_class="A" if cumulated <= PARETO_A + PERCENT_TOLERANCE else "B" if cumulated <= PARETO_B + PERCENT_TOLERANCE else "C",
            )
        nva = self.env.ref("cmc_base.cmc_attribute_value_nva", raise_if_not_found=False)
        nva_activities = {row["activity_id"] for row in rows if nva and row["va_class_value_id"] == nva.id}
        waste = defaultdict(float)
        for flow in flows:
            if flow["stage"] in ("object", "sustaining") and flow.get("source_activity_id") in nva_activities:
                waste[flow["target_object_id"]] += flow["amount"]
        for oid, result in results.items():
            result["nva_cost"] = float_round(waste.get(oid, 0.0), precision_rounding=rounding)

    def _cmc_performance_vals(self, rows):
        """One performance row per computed activity that declares measures; facts read grouped."""
        lines = self.env["cmc.abc.activity.measure"].search([("activity_id", "in", [row["activity_id"] for row in rows])])
        if not lines:
            return []
        measure_of = defaultdict(dict)  # activity id -> {role: measure id}
        for line in lines:
            measure_of[line.activity_id.id][line.role] = line.measure_id.id
        # why sudo: facts of the run company are engine input, whoever presses Compute
        groups = self.env["cmc.measure.quantity"].sudo()._read_group(
            self._cmc_fact_domain([("activity_id", "in", lines.activity_id.ids), ("driver_id", "in", lines.measure_id.ids)]),
            ["activity_id", "driver_id"], ["quantity:sum"],
        )
        facts = {(activity.id, driver.id): qty for activity, driver, qty in groups}
        month = month_start(self.date_from) if self.date_from else False
        vals = []
        for row in rows:
            roles = measure_of.get(row["activity_id"])
            if not roles:
                continue
            fact = {role: facts.get((row["activity_id"], measure), 0.0) for role, measure in roles.items()}
            output = fact.get("output", 0.0) if "output" in roles else row["used_qty"]
            vals.append({
                "run_id": self.id, "activity_id": row["activity_id"], "month": month, "activity_cost": row["total_cost"],
                "output_qty": output, "cost_per_output": row["total_cost"] / output if output else 0.0,
                "cycle_time": fact.get("cycle_time", 0.0) / output if output else 0.0,
                "defect_qty": fact.get("quality", 0.0), "quality_rate": fact.get("quality", 0.0) / output * 100.0 if output else 0.0,
            })
        return vals

    # ---- ABM reads (stored results, grouped) ------------------------------------

    def _cmc_activity_center_matrix(self, by="activity"):
        """{(activity|process id, center id): stage-1 cost}: the activity x department matrix."""
        self.ensure_one()
        process_of = {row.activity_id.id: row.process_id.id for row in self.activity_cost_ids} if by == "process" else {}
        groups = self.env["cmc.abc.flow"]._read_group(
            [("run_id", "=", self.id), ("stage", "in", ("resource", "tag"))], ["target_activity_id", "center_id"], ["amount:sum"]
        )
        matrix = defaultdict(float)
        for activity, center, amount in groups:
            matrix[(process_of[activity.id] if by == "process" else activity.id, center.id)] += amount
        return dict(matrix)

    def _cmc_activity_values(self, attribute):
        """{activity id: attribute value id or False}: the compute snapshot of the shipped classes, else the live value."""
        self.ensure_one()
        attribute = self.env["cmc.abc.activity"]._cmc_resolve_attribute(attribute)
        field = ATTRIBUTE_SNAPSHOTS.get(attribute.code) if not attribute.company_id else None
        if field:
            return {row.activity_id.id: row[field].id for row in self.activity_cost_ids}
        groups = self.activity_cost_ids.activity_id._cmc_group_by_attribute(attribute)
        return {act: val for val, ids in groups.items() for act in ids}

    def _cmc_object_cost_by_attribute(self, attribute):
        """{cost object id: {value id or False: amount}} of the activity -> object flows, by the class of the CHARGING activity."""
        self.ensure_one()
        value_of = self._cmc_activity_values(attribute)
        groups = self.env["cmc.abc.flow"]._read_group(
            [("run_id", "=", self.id), ("stage", "in", ("object", "sustaining")), ("source_activity_id", "!=", False)],
            ["target_object_id", "source_activity_id"], ["amount:sum"],
        )
        costs = defaultdict(lambda: defaultdict(float))
        for obj, activity, amount in groups:
            costs[obj.id][value_of.get(activity.id, False)] += amount
        return {oid: dict(by_value) for oid, by_value in costs.items()}

    def _cmc_activity_pareto(self):
        """[{activity, cost, share, cumulative_share, rank, pareto_class}] in rank order."""
        self.ensure_one()
        return [
            {
                "activity": row.activity_id, "cost": row.resource_cost, "share": row.cost_share,
                "cumulative_share": row.cumulative_share, "rank": row.pareto_rank, "pareto_class": row.pareto_class,
            }
            for row in self.activity_cost_ids.sorted("pareto_rank")
        ]

    def _cmc_activity_cost_quadrant(self, attr_x, attr_y):
        """{(x value id or False, y value id or False): stage-1 cost}."""
        self.ensure_one()
        x_of, y_of = self._cmc_activity_values(attr_x), self._cmc_activity_values(attr_y)
        costs = defaultdict(float)
        for row in self.activity_cost_ids:
            costs[(x_of.get(row.activity_id.id, False), y_of.get(row.activity_id.id, False))] += row.resource_cost
        return dict(costs)

    # ---- cost of quality --------------------------------------------------------

    def _cmc_coq_splits(self):
        """{run id: split of the activity cost by cost-of-quality class} from the compute snapshot."""
        splits = {run.id: dict.fromkeys(COQ_BUCKETS, 0.0) for run in self}
        groups = self.env["cmc.abc.activity.cost"]._read_group(
            [("run_id", "in", self.ids)], ["run_id", "coq_value_id"], ["resource_cost:sum"]
        )
        for run, value, cost in groups:
            splits[run.id][value.code if value.code in COQ_BUCKETS else "unclassified"] += cost
        for split in splits.values():
            split["conformance"] = split["prevention"] + split["appraisal"]
            split["nonconformance"] = split["internal_failure"] + split["external_failure"]
            split["coq_total"] = split["conformance"] + split["nonconformance"]
            split["total"] = sum(split[bucket] for bucket in COQ_BUCKETS)
            split["coq_percent"] = split["coq_total"] / split["total"] * 100.0 if split["total"] else 0.0
        return splits

    def _cmc_coq_split(self):
        self.ensure_one()
        return self._cmc_coq_splits()[self.id]

    @api.model
    def _cmc_month_runs(self, date_from, date_to):
        """{month: run}: the most recent done run of the current company starting in each month."""
        runs = self.search([
            ("state", "=", "done"), ("company_id", "=", self.env.company.id),
            ("date_from", ">=", month_start(date_from)), ("date_from", "<=", date_to),
        ], order="id")
        return {month_start(run.date_from): run for run in runs}

    @api.model
    def _cmc_coq_trend(self, date_from, date_to):
        runs = self._cmc_month_runs(date_from, date_to)
        splits = self.browse([run.id for run in runs.values()])._cmc_coq_splits()
        return [{"month": month, **splits[run.id]} for month, run in sorted(runs.items())]


class CmcAbmInitiative(models.Model):
    _name = "cmc.abm.initiative"
    _description = "ABM Improvement Initiative"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    activity_id = fields.Many2one(
        "cmc.abc.activity", required=True, check_company=True, index=True, domain="[('node_type', '=', 'activity')]"
    )
    project_type = fields.Selection(
        [("eliminate", "Eliminate"), ("substitute", "Substitute"), ("reduce", "Reduce"), ("share", "Share")]
    )
    owner_id = fields.Many2one("res.users", string="Owner")
    baseline_run_id = fields.Many2one(
        "cmc.abc.run", string="Baseline Run", required=True, check_company=True, ondelete="restrict", domain="[('state', '=', 'done')]"
    )
    review_run_id = fields.Many2one(
        "cmc.abc.run", string="Review Run", check_company=True, ondelete="restrict", domain="[('state', '=', 'done')]",
        help="Run that repeats the model after the improvement, to verify the benefit.",
    )
    expected_driver_reduction = fields.Float(string="Expected Reduction (%)")
    baseline_annual_cost = fields.Monetary(compute="_compute_baseline_annual_cost", store=True)
    expected_annual_saving = fields.Monetary(compute="_compute_expected_annual_saving", store=True, readonly=False)
    one_time_cost = fields.Monetary()
    payback_years = fields.Float(compute="_compute_payback", store=True, digits=(16, 4))
    payback_months = fields.Float(compute="_compute_payback", store=True, digits=(16, 2))
    realized_annual_saving = fields.Monetary(compute="_compute_benefit", store=True)
    realization_percent = fields.Float(compute="_compute_benefit", store=True, digits=(16, 2))
    freed_capacity_qty = fields.Float(compute="_compute_benefit", store=True)
    freed_capacity_cost = fields.Monetary(
        compute="_compute_benefit", store=True, help="Freed quantity x baseline rate: not a saving until spending falls."
    )
    resource_action = fields.Selection([("grow", "Grow the business"), ("redeploy", "Redeploy"), ("remove", "Remove")])
    benefit_status = fields.Selection(
        [("pending", "Pending"), ("not_realized", "Not realized"), ("partial", "Partially realized"), ("realized", "Realized")],
        compute="_compute_benefit", store=True,
    )

    @api.constrains("activity_id", "baseline_run_id", "review_run_id")
    def _check_initiative(self):
        for initiative in self:
            if initiative.activity_id.node_type != "activity":
                raise ValidationError(self.env._("An initiative targets an activity, not a process or a task."))
            if (initiative.baseline_run_id | initiative.review_run_id).filtered(lambda run: run.state != "done"):
                raise ValidationError(self.env._("Baseline and review must be computed runs."))
            if initiative.review_run_id == initiative.baseline_run_id:
                raise ValidationError(self.env._("The review run must differ from the baseline run."))

    def _cmc_figures(self):
        """{(run id, activity id): (total cost, used qty, rate)} in one grouped read."""
        runs = self.baseline_run_id | self.review_run_id
        groups = self.env["cmc.abc.activity.cost"]._read_group(
            [("run_id", "in", runs.ids), ("activity_id", "in", self.activity_id.ids)],
            ["run_id", "activity_id"], ["total_cost:sum", "used_qty:sum", "rate:sum"],
        )
        return {(run.id, activity.id): figures for run, activity, *figures in groups}

    def _cmc_figure(self, figures, run, index):
        return figures.get((run.id, self.activity_id.id), (0.0, 0.0, 0.0))[index]

    def _cmc_monthly_cost(self, figures, run):
        return self._cmc_figure(figures, run, 0) / run._cmc_span_months() if run else 0.0

    @api.depends(
        "activity_id", "baseline_run_id.date_from", "baseline_run_id.date_to", "baseline_run_id.activity_cost_ids.total_cost"
    )
    def _compute_baseline_annual_cost(self):
        figures = self._cmc_figures()
        for initiative in self:
            initiative.baseline_annual_cost = initiative._cmc_monthly_cost(figures, initiative.baseline_run_id) * 12

    @api.depends("baseline_annual_cost", "expected_driver_reduction")
    def _compute_expected_annual_saving(self):
        for initiative in self:
            # a typed (non-empty) saving is never overwritten: the reduction only defaults it
            if not initiative.expected_annual_saving:
                initiative.expected_annual_saving = initiative.baseline_annual_cost * initiative.expected_driver_reduction / 100.0

    @api.depends("one_time_cost", "expected_annual_saving")
    def _compute_payback(self):
        for initiative in self:
            initiative.payback_years = simple_payback(initiative.one_time_cost, initiative.expected_annual_saving)
            initiative.payback_months = initiative.payback_years * 12

    @api.depends(
        "activity_id", "expected_annual_saving",
        "baseline_run_id.date_from", "baseline_run_id.date_to", "baseline_run_id.activity_cost_ids.total_cost",
        "baseline_run_id.activity_cost_ids.used_qty", "baseline_run_id.activity_cost_ids.rate",
        "review_run_id.date_from", "review_run_id.date_to", "review_run_id.activity_cost_ids.total_cost",
        "review_run_id.activity_cost_ids.used_qty",
    )
    def _compute_benefit(self):
        figures = self._cmc_figures()
        for initiative in self:
            baseline, review = initiative.baseline_run_id, initiative.review_run_id
            monthly = initiative._cmc_monthly_cost
            realized = (monthly(figures, baseline) - monthly(figures, review)) * 12 if review else 0.0
            expected = initiative.expected_annual_saving
            percent = realized / expected * 100.0 if review and expected > 0 else 0.0
            freed = initiative._cmc_figure(figures, baseline, 1) - initiative._cmc_figure(figures, review, 1) if review else 0.0
            initiative.realized_annual_saving = realized
            initiative.realization_percent = percent
            initiative.freed_capacity_qty = freed
            initiative.freed_capacity_cost = freed * initiative._cmc_figure(figures, baseline, 2)
            initiative.benefit_status = (
                "pending" if not review else "not_realized" if realized <= 0
                else "partial" if float_compare(percent, 100.0, precision_digits=2) < 0 else "realized"
            )


class CmcAbmBenchmark(models.Model):
    _name = "cmc.abm.benchmark"
    _description = "ABM Activity Benchmark"
    _order = "id desc"

    name = fields.Char(required=True)
    # optional: a benchmark may compare several companies
    company_id = fields.Many2one("res.company", index=True)
    run_ids = fields.Many2many("cmc.abc.run", string="Runs", domain="[('state', '=', 'done')]")
    company_ids = fields.Many2many(
        "res.company", string="Companies", compute="_compute_company_ids", store=True,
        help="Companies of the runs: the record rule shows the benchmark only to users who see all of them.",
    )
    line_ids = fields.One2many("cmc.abm.benchmark.line", "benchmark_id", string="Lines")

    @api.depends("run_ids.company_id")
    def _compute_company_ids(self):
        for bench in self:
            bench.company_ids = bench.run_ids.company_id

    def _cmc_check_scope(self):
        """No silent filtering: runs outside the active companies, or in mixed currencies, refuse the compute."""
        for bench in self:
            if bench.run_ids.company_id - self.env.companies:
                raise UserError(self.env._("%s: some runs belong to companies that are not active.", bench.display_name))
            if len(bench.run_ids.currency_id) > 1:
                raise UserError(self.env._("%s: the runs have different currencies, unit costs are not comparable.", bench.display_name))

    def action_compute(self):
        self._cmc_check_scope()
        for bench in self:
            if bench.run_ids.filtered(lambda run: run.state != "done"):
                raise UserError(self.env._("Only computed runs can be benchmarked."))
            costs = self.env["cmc.abc.activity.cost"].search([("run_id", "in", bench.run_ids.ids), ("used_qty", ">", 0)])
            best = {}
            for cost in costs:
                code = cost.activity_id.code
                best[code] = min(best.get(code, cost.rate), cost.rate)
            vals = []
            for cost in costs:
                top = best[cost.activity_id.code]
                vals.append({
                    "benchmark_id": bench.id, "run_id": cost.run_id.id, "activity_id": cost.activity_id.id,
                    "activity_code": cost.activity_id.code, "unit_cost": cost.rate, "best_unit_cost": top,
                    "gap_percent": (cost.rate - top) / top * 100.0 if top else 0.0,
                    "is_best": not float_compare(cost.rate, top, precision_digits=6),
                })
            # why sudo: the lines are results of this action; users only read them
            bench.line_ids.sudo().unlink()
            self.env["cmc.abm.benchmark.line"].sudo().create(vals)
        return True


class CmcAbmBenchmarkLine(models.Model):
    _name = "cmc.abm.benchmark.line"
    _description = "ABM Benchmark Line"
    _order = "activity_code, unit_cost"

    benchmark_id = fields.Many2one("cmc.abm.benchmark", required=True, ondelete="cascade", index=True)
    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    period = fields.Date(related="run_id.date_from", store=True)
    activity_id = fields.Many2one("cmc.abc.activity", ondelete="set null")
    activity_code = fields.Char(index=True)
    unit_cost = fields.Float(digits=(16, 4))
    best_unit_cost = fields.Float(digits=(16, 4))
    gap_percent = fields.Float(digits=(16, 4), help="(unit cost - best) / best x 100.")
    is_best = fields.Boolean()
