import ast
from collections import defaultdict
from datetime import timedelta
from graphlib import CycleError, TopologicalSorter

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools.safe_eval import _BUILTINS, safe_eval

from odoo.addons.cmc_base.models.cmc_utils import format_figure, month_end, month_range, month_start

from .cmc_kpi import PERSPECTIVES
from .cmc_kpi_run_snapshot import BVA_KEYS

TOLERANCE = 0.01  # percent points: weights are typed by hand
TREND_EPSILON = 1e-6
MAX_SCORE = 150.0
CASCADE_TARGET = 100.0


def _eval_cmc_rate(run, kpis, month):
    """EUR/h style center coefficient: the run-published rate of (center, driver, month), in one search."""
    usable = kpis.filtered(lambda kpi: kpi.center_id and kpi.driver_id)
    rates = run.env["cmc.rate"].search([
        ("holder_model", "=", "account.analytic.account"), ("holder_id", "in", usable.center_id.ids),
        ("driver_id", "in", usable.driver_id.ids), ("component", "=", "total"), ("date", "=", month),
        ("company_id", "=", run.company_id.id), ("origin_model", "!=", "cmc.plan.version"),
    ], order="id desc")
    latest = {}
    for rate in rates:
        latest.setdefault((rate.holder_id, rate.driver_id.id), rate)
    found = {kpi.id: latest.get((kpi.center_id.id, kpi.driver_id.id)) for kpi in usable}
    origins = defaultdict(set)
    for rate in found.values():
        if rate:
            origins[rate.origin_model].add(rate.origin_id)
    for model, ids in origins.items():
        # why sudo: the certified origin must be seen whatever the rules of the caller
        run._cmc_record_usage(run.env[model].sudo().browse(ids))
    return {kpi.id: found[kpi.id].value if found.get(kpi.id) else None for kpi in kpis}


def _measure(kpi, value, target, direction):
    """(gap %, status, score) of a value against its target; a 0 target compares the plain difference."""
    diff = value - target if direction == "higher" else target - value
    gap = diff / abs(target) * 100.0 if target else diff
    if gap >= -kpi.amber_tolerance_pct:
        status = "green"
    elif gap >= -kpi.red_tolerance_pct:
        status = "amber"
    else:
        status = "red"
    return gap, status, min(max(100.0 + gap, 0.0), MAX_SCORE)


class CmcKpiRun(models.Model):
    _name = "cmc.kpi.run"
    _inherit = ["cmc.run.mixin"]
    _description = "KPI Measurement Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = ("company_id", "date_from", "date_to", "version_id", "fin_analysis_id", "bva_version_id", "kpi_ids", "scorecard_ids")
    _cmc_result_fields = ("value_ids", "scorecard_result_ids", "bva_ids", "ratio_ids")

    name = fields.Char(required=True, default=lambda self: self.env._("KPI measurement"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    date_from = fields.Date(required=True, default=lambda self: fields.Date.today().replace(day=1))
    date_to = fields.Date(required=True, default=lambda self: fields.Date.today().replace(day=1))
    version_id = fields.Many2one(
        "cmc.plan.version", string="Target Version", ondelete="restrict", check_company=True,
        help="Plan version holding the KPI targets; without a target the static one applies.",
    )
    fin_analysis_id = fields.Many2one(
        "cmc.fin.analysis", string="Financial Analysis", ondelete="restrict", check_company=True,
        help="Source of the ratio KPIs and of the key ratios of the pack; empty = the latest computed analysis.",
    )
    bva_version_id = fields.Many2one(
        "cmc.plan.version", string="Budget for the Pack", ondelete="restrict", check_company=True,
        domain=[("version_type", "in", ("budget", "forecast"))],
    )
    kpi_ids = fields.Many2many("cmc.kpi", "cmc_kpi_run_kpi_rel", "run_id", "kpi_id", string="KPIs", check_company=True, help="Empty = every active KPI.")
    scorecard_ids = fields.Many2many(
        "cmc.kpi.scorecard", "cmc_kpi_run_scorecard_rel", "run_id", "scorecard_id", string="Scorecards", check_company=True,
        help="Empty = every active scorecard.",
    )
    value_ids = fields.One2many("cmc.kpi.value", "run_id")
    scorecard_result_ids = fields.One2many("cmc.kpi.scorecard.result", "run_id")
    bva_ids = fields.One2many("cmc.kpi.run.bva", "run_id")
    ratio_ids = fields.One2many("cmc.kpi.run.ratio", "run_id")

    @api.constrains("date_from", "date_to")
    def _check_dates_ordered(self):
        if any(run.date_to < run.date_from for run in self):
            raise ValidationError(self.env._("The last month cannot precede the first month."))

    @api.model
    def _cmc_kpi_evaluators(self):
        """Engines register here (``super()`` + their key): ``callable(run, kpis, month) -> {kpi.id: float | None}``."""
        return {"cmc_rate": _eval_cmc_rate}

    # ---- compute --------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        kpis = self._cmc_kpis()
        scorecards = self._cmc_scorecards()
        months = month_range(self.date_from, self.date_to)
        # all the checks before the first row is written
        ordered, children, formula_names = self._cmc_kpi_plan(kpis)
        self._cmc_check_scorecards(scorecards)
        analyses = self._cmc_analyses(kpis, months)
        sources, no_data = self._cmc_source_values(kpis, months, analyses)
        rows = self._cmc_rows(ordered, children, formula_names, months, sources, no_data)
        self._cmc_record_sources(kpis, analyses)
        # why sudo: result rows are engine output (users hold read access only)
        self.env["cmc.kpi.value"].sudo().create(list(rows.values()))
        self.env["cmc.kpi.scorecard.result"].sudo().create(self._cmc_scorecard_rows(scorecards, months, rows))
        self.env["cmc.kpi.run.bva"].sudo().create(self._cmc_bva_rows(months))
        self.env["cmc.kpi.run.ratio"].sudo().create(self._cmc_ratio_rows(analyses))

    def _cmc_kpis(self):
        """The selected KPIs (empty = every active one) plus, transitively, what their formulas read and their cascade children."""
        Kpi = self.env["cmc.kpi"]
        active = Kpi.search([("company_id", "=", self.company_id.id)])
        by_code = {kpi.code: kpi for kpi in active}
        kpis = todo = self.kpi_ids or active
        while todo:
            needed = Kpi
            for kpi in todo:
                if kpi.source_type == "formula":
                    needed |= Kpi.union(*(by_code[name] for name in self._cmc_formula_names(kpi) if name in by_code))
                elif kpi.source_type == "cascade":
                    needed |= kpi.child_ids
            todo = needed - kpis
            kpis |= todo
        return kpis

    @api.model
    def _cmc_formula_names(self, kpi):
        return {node.id for node in ast.walk(ast.parse(kpi.formula.strip(), mode="eval")) if isinstance(node, ast.Name)} - set(_BUILTINS)

    def _cmc_analyses(self, kpis, months):
        """{year: cmc.fin.analysis} the pack and the ratio KPIs read: the one named by the run, else the latest computed one of the year."""
        years = {month.year for month in months}
        if not self.fin_analysis_id and not kpis.filtered(lambda kpi: kpi.source_type == "ratio"):
            return {}
        analyses = self.fin_analysis_id or self.env["cmc.fin.analysis"].search([
            ("company_id", "=", self.company_id.id), ("state", "=", "done"),
            ("year_from", "<=", max(years)), ("year_to", ">=", min(years)),
        ], order="id desc")
        by_year = {year: next((a for a in analyses if a.year_from <= year <= a.year_to), None) for year in years}
        return {year: analysis for year, analysis in by_year.items() if analysis}

    def _cmc_record_sources(self, kpis, analyses):
        """Pin (AD-002) every computed source the run reads; a draft one raises."""
        Analysis = self.env["cmc.fin.analysis"]
        versions = kpis.filtered(lambda kpi: kpi.source_type == "variance").variance_version_id | self.bva_version_id
        # a draft target version stays allowed: it is not pinned
        versions |= self.version_id.filtered(lambda version: version.state == "done")
        for origins in (self.fin_analysis_id | Analysis.union(*analyses.values()), versions):
            if origins:
                self._cmc_record_usage(origins)

    def _cmc_scorecards(self):
        return self.scorecard_ids or self.env["cmc.kpi.scorecard"].search([("company_id", "=", self.company_id.id)])

    def _cmc_kpi_plan(self, kpis):
        """Evaluation order of the KPIs and the structures it needs; unknown names, unknown engines, cascade weights and cycles raise."""
        codes = {kpi.code: kpi for kpi in kpis}
        archived = {
            code for (code,) in self.env["cmc.kpi"].with_context(active_test=False)._read_group(
                [("company_id", "=", self.company_id.id), ("active", "=", False)], ["code"])
        }
        evaluators = self._cmc_kpi_evaluators()
        children = kpis.grouped("parent_id")
        formula_names, graph = {}, {}
        for kpi in kpis:
            graph[kpi.id] = set()
            if kpi.source_type == "formula":
                names = self._cmc_formula_names(kpi)
                if gone := sorted((names & archived) - set(codes)):
                    raise UserError(self.env._("%(code)s reads the archived KPI %(names)s.", code=kpi.code, names=", ".join(gone)))
                if unknown := sorted(names - set(codes)):
                    raise UserError(self.env._("%(code)s: unknown names in the formula: %(names)s", code=kpi.code, names=", ".join(unknown)))
                formula_names[kpi.id] = names
                graph[kpi.id] = {codes[name].id for name in names if name in codes}
            elif kpi.source_type == "variance" and kpi.variance_version_id.state != "done":
                raise UserError(self.env._("%(code)s: the budget version %(version)s is not computed.", code=kpi.code, version=kpi.variance_version_id.display_name))
            elif kpi.source_type == "hook" and kpi.hook_key not in evaluators:
                raise UserError(self.env._("%(code)s: no engine registered for the key %(key)s.", code=kpi.code, key=kpi.hook_key))
            elif kpi.source_type == "cascade" and (kids := children.get(kpi)):
                if abs(sum(kids.mapped("cascade_weight")) - 100.0) > TOLERANCE:
                    raise UserError(self.env._("%(code)s: the weights of the child KPIs do not total 100.", code=kpi.code))
                graph[kpi.id] = set(kids.ids)
        try:
            order = list(TopologicalSorter(graph).static_order())
        except CycleError as error:
            by_id = {kpi.id: kpi.code for kpi in kpis}
            raise UserError(self.env._("These KPIs depend on each other: %s", ", ".join(by_id[kpi_id] for kpi_id in error.args[1]))) from error
        return kpis.browse(order), children, formula_names

    def _cmc_check_scorecards(self, scorecards):
        for scorecard in scorecards:
            weights = {line.perspective: line.weight for line in scorecard.perspective_ids}
            if abs(sum(weights.values()) - 100.0) > TOLERANCE:
                raise UserError(self.env._("Scorecard %s: the perspective weights do not total 100.", scorecard.name))
            totals = defaultdict(float)
            for line in scorecard.line_ids:
                if line.perspective not in weights:
                    raise UserError(self.env._("Scorecard %(name)s: %(kpi)s sits in a perspective with no weight.", name=scorecard.name, kpi=line.kpi_id.code))
                totals[line.perspective] += line.weight
            for perspective in weights:
                if abs(totals[perspective] - 100.0) > TOLERANCE:
                    raise UserError(self.env._(
                        "Scorecard %(name)s: the KPI weights of the perspective %(perspective)s do not total 100.",
                        name=scorecard.name, perspective=dict(PERSPECTIVES)[perspective],
                    ))

    # ---- sources (one batch per source type, whatever the number of KPIs) ----

    def _cmc_source_values(self, kpis, months, analyses):
        """({(kpi.id, month): float | None}, {(kpi.id, month) without any fact}) of the KPIs read from the data."""
        values = {}
        measures, no_data = self._cmc_measure_values(kpis.filtered(lambda kpi: kpi.source_type == "measure"), months)
        values.update(measures)
        values.update(self._cmc_ratio_values(kpis.filtered(lambda kpi: kpi.source_type == "ratio"), months, analyses))
        values.update(self._cmc_variance_values(kpis.filtered(lambda kpi: kpi.source_type == "variance"), months))
        hooks = kpis.filtered(lambda kpi: kpi.source_type == "hook")
        evaluators = self._cmc_kpi_evaluators()
        for key, group in hooks.grouped("hook_key").items():
            for month in months:
                values.update({(kpi_id, month): value for kpi_id, value in evaluators[key](self, group, month).items()})
        return values, no_data

    def _cmc_measure_values(self, kpis, months):
        if not kpis:
            return {}, set()
        drivers = kpis.driver_id | kpis.denominator_driver_id
        by_center, by_object = bool(kpis.center_id), bool(kpis.cost_object_id)
        groupby = ["driver_id", "date:month"] + ["center_id"] * by_center + ["cost_object_id"] * by_object
        domain = Domain([
            ("version_id", "=", False), ("company_id", "=", self.company_id.id), ("driver_id", "in", drivers.ids),
            ("date", ">=", months[0]), ("date", "<=", months[-1]),
        ])
        facts = defaultdict(list)  # (driver, month) -> [(center, object, sum, rows)]
        for driver, month, *scope, total, count in self.env["cmc.measure.quantity"]._read_group(domain, groupby, ["quantity:sum", "__count"]):
            scope = iter(scope)
            center = next(scope).id if by_center else False
            cost_object = next(scope).id if by_object else False
            facts[driver.id, fields.Date.to_date(month)].append((center, cost_object, total or 0.0, count))

        def scoped(kpi, driver, month):
            return [
                (total, count) for center, cost_object, total, count in facts.get((driver.id, month), ())
                if (not kpi.center_id or center == kpi.center_id.id) and (not kpi.cost_object_id or cost_object == kpi.cost_object_id.id)
            ]

        def has_fact(driver, kpi, month):
            return bool(scoped(kpi, driver, month))

        def aggregate(kpi, driver, month):
            rows = scoped(kpi, driver, month)
            if not rows:
                return 0.0 if kpi.aggregation == "sum" else None
            total, count = sum(row[0] for row in rows), sum(row[1] for row in rows)
            return total if kpi.aggregation == "sum" else total / count

        values, no_data = {}, set()
        for kpi in kpis:
            for month in months:
                value = aggregate(kpi, kpi.driver_id, month)
                # why: a month without any fact of the driver the value is divided by (else of its own) has no data to judge
                if not has_fact(kpi.denominator_driver_id or kpi.driver_id, kpi, month):
                    no_data.add((kpi.id, month))
                if value is not None and kpi.denominator_driver_id:
                    denominator = aggregate(kpi, kpi.denominator_driver_id, month)
                    value = value / denominator if denominator else None
                values[kpi.id, month] = value * kpi.scale if value is not None else None
        return values, no_data

    def _cmc_ratio_values(self, kpis, months, analyses):
        if not kpis:
            return {}
        rows = self.env["cmc.fin.ratio.value"].search([
            ("run_id", "in", [a.id for a in analyses.values()]), ("year", "in", list(analyses)), ("code", "in", kpis.mapped("ratio_code")),
        ])
        found = {(row.run_id.id, row.year, row.code): row for row in rows if row.status != "not_computable"}
        values = {}
        for kpi in kpis:
            for month in months:
                analysis = analyses.get(month.year)
                row = found.get((analysis.id, month.year, kpi.ratio_code)) if analysis else None
                values[kpi.id, month] = row.value if row else None
        return values

    def _cmc_variance_values(self, kpis, months):
        summaries, values = {}, {}
        for kpi in kpis:
            groupby = ("center_id", "element_id") if kpi.element_id else ("center_id",)
            key = (kpi.center_id, kpi.element_id)[: len(groupby)]
            for month in months:
                cache_key = (kpi.variance_version_id.id, month, groupby)
                if cache_key not in summaries:
                    rows = kpi.variance_version_id._cmc_bva_summary(month_end(month), groupby)
                    summaries[cache_key] = {tuple(row[name] for name in groupby): row for row in rows}
                row = summaries[cache_key].get(key)
                basis = kpi.variance_basis or "month"
                values[kpi.id, month] = row[f"variance_{basis}_pct"] if row and row[f"plan_{basis}"] else None
        return values

    # ---- rows -----------------------------------------------------------

    def _cmc_targets(self, kpis, months):
        if not self.version_id:
            return {}
        targets = self.env["cmc.kpi.target"].search([("version_id", "=", self.version_id.id), ("kpi_id", "in", kpis.ids), ("date", "in", months)])
        return {(target.kpi_id.id, target.date): target.target_value for target in targets}

    def _cmc_previous_values(self, kpis, months):
        """Latest computed value of the month before each month, from other runs (one grouped read)."""
        previous = sorted({month_start(month - timedelta(days=1)) for month in months})
        Value = self.env["cmc.kpi.value"]
        groups = Value._read_group(
            [("run_id", "!=", self.id), ("company_id", "=", self.company_id.id), ("computable", "=", True),
             ("kpi_id", "in", kpis.ids), ("date", "in", previous)],
            ["kpi_id", "date:month"], ["id:max"],
        )
        return {(row.kpi_id.id, row.date): row.value for row in Value.browse([group[2] for group in groups])}

    def _cmc_rows(self, ordered, children, formula_names, months, sources, no_data=frozenset()):
        targets = self._cmc_targets(ordered, months)
        others = self._cmc_previous_values(ordered, months)
        rows = {}
        for month in months:
            current, empty_codes = {}, set()
            previous_month = month_start(month - timedelta(days=1))
            for kpi in ordered:
                if kpi.source_type == "formula":
                    value = self._cmc_eval_formula(kpi, formula_names[kpi.id], current)
                elif kpi.source_type == "cascade":
                    value = self._cmc_cascade_value(children.get(kpi, ordered.browse()), rows, month)
                else:
                    value = sources.get((kpi.id, month))
                current[kpi.code] = value
                empty = (kpi.id, month) in no_data
                # why: a formula over inputs that all have no data (produced - reworked, both empty) has no data either
                inputs_empty = kpi.source_type == "formula" and formula_names[kpi.id] and set(formula_names[kpi.id]) <= empty_codes
                empty = empty or bool(inputs_empty)
                if empty:
                    empty_codes.add(kpi.code)
                reason = False if value is not None and not empty else self._cmc_reason(kpi, formula_names.get(kpi.id), current, empty)
                if inputs_empty:
                    reason = self.env._("No data for %s", ", ".join(sorted(formula_names[kpi.id])))
                if kpi.source_type == "cascade":
                    target, source, direction = CASCADE_TARGET, "static", "higher"
                elif (kpi.id, month) in targets:
                    target, source, direction = targets[kpi.id, month], "version", kpi.direction
                else:
                    target, source, direction = kpi.target_value, "static", kpi.direction
                before = rows.get((kpi.id, previous_month))
                before = before["value"] if before and before["computable"] else others.get((kpi.id, previous_month))
                rows[kpi.id, month] = self._cmc_row(kpi, month, value, (target, source, direction), before, reason, empty)
        return rows

    def _cmc_row(self, kpi, month, value, target, before, reason, empty=False):
        target, source, direction = target
        vals = {
            "run_id": self.id, "company_id": self.company_id.id, "kpi_id": kpi.id, "code": kpi.code, "perspective": kpi.perspective,
            "responsibility_id": kpi.responsibility_id.id, "date": month, "value": value or 0.0, "computable": value is not None, "reason": reason,
            "target": target, "target_source": source, "gap_pct": 0.0, "status": "none", "score": 0.0,
            "previous_value": 0.0, "trend_delta": 0.0, "trend": "none",
        }
        # why: a formula returning a real 0 against a 0 target (0 ppm) is on target; its inputs already carry the no_data
        if empty or (value is not None and not value and not target and kpi.source_type != "formula"):
            return vals | {"computable": False, "status": "no_data", "reason": reason or self.env._("No data for the month")}
        if value is None:
            return vals
        gap, status, score = _measure(kpi, value, target, direction)
        vals |= {"gap_pct": gap, "status": status, "score": score}
        if before is not None:
            delta = value - before
            better = delta if direction == "higher" else -delta
            trend = "stable" if abs(delta) < TREND_EPSILON else "improving" if better > 0 else "worsening"
            vals |= {"previous_value": before, "trend_delta": delta, "trend": trend}
        return vals

    def _cmc_reason(self, kpi, names, current, no_data=False):
        if no_data:
            return self.env._("No data for the month")
        if kpi.source_type == "formula":
            missing = sorted(name for name in names if current.get(name) is None)
            if missing:
                return self.env._("%s is not computable", ", ".join(missing))
            return self.env._("the formula cannot be evaluated")
        if kpi.source_type == "cascade":
            return self.env._("no computable child KPI for %s", kpi.code)
        return self.env._("no data for %s", kpi.code)

    def _cmc_eval_formula(self, kpi, names, current):
        namespace = {name: current.get(name) for name in names}
        if any(value is None for value in namespace.values()):
            return None
        try:
            return float(safe_eval(kpi.formula.strip(), namespace))
        except (ValueError, ZeroDivisionError, TypeError):
            return None

    def _cmc_cascade_value(self, kids, rows, month):
        weighted = [(kid.cascade_weight, rows[kid.id, month]["score"]) for kid in kids if rows[kid.id, month]["computable"]]
        total = sum(weight for weight, _score in weighted)
        return sum(weight * score for weight, score in weighted) / total if total else None

    def _cmc_scorecard_rows(self, scorecards, months, rows):
        results = []
        for scorecard in scorecards:
            weights = {line.perspective: line.weight for line in scorecard.perspective_ids}
            lines = scorecard.line_ids.grouped("perspective")
            for month in months:
                overall = [0.0, 0.0]
                for perspective, weight in weights.items():
                    scored = [
                        (line.weight, rows[line.kpi_id.id, month]["score"]) for line in lines.get(perspective, ())
                        if (line.kpi_id.id, month) in rows and rows[line.kpi_id.id, month]["computable"]
                    ]
                    score = self._cmc_weighted(scored)
                    results.append(self._cmc_scorecard_row(scorecard, month, perspective, weight, score))
                    if score is not None:
                        overall[0] += weight * score
                        overall[1] += weight
                results.append(self._cmc_scorecard_row(scorecard, month, False, 100.0, overall[0] / overall[1] if overall[1] else None))
        return results

    @api.model
    def _cmc_weighted(self, scored):
        total = sum(weight for weight, _score in scored)
        return sum(weight * score for weight, score in scored) / total if total else None

    def _cmc_scorecard_row(self, scorecard, month, perspective, weight, score):
        return {
            "run_id": self.id, "company_id": self.company_id.id, "scorecard_id": scorecard.id, "date": month,
            "perspective": perspective, "weight": weight, "score": score or 0.0, "computable": score is not None,
        }

    # ---- snapshot of the pack sources (RN-002) ----

    def _cmc_bva_rows(self, months):
        if not self.bva_version_id:
            return []
        return [
            {"run_id": self.id, "company_id": self.company_id.id, "date": month, "center_id": row["center_id"].id if row["center_id"] else False}
            | {key: row[key] for key in BVA_KEYS}
            for month in months for row in self.bva_version_id._cmc_bva_summary(month_end(month), ("center_id",))
        ]

    def _cmc_ratio_rows(self, analyses):
        if not analyses:
            return []
        by_analysis = {analysis.id: analysis for analysis in analyses.values()}
        values = self.env["cmc.fin.ratio.value"].search([("run_id", "in", list(by_analysis)), ("year", "in", list(analyses))])
        return [
            {
                "run_id": self.id, "company_id": self.company_id.id, "fin_analysis_id": row.run_id.id, "year": row.year,
                "code": row.code, "name": row.name, "value": row.value, "status": row.status,
            }
            for row in values if analyses[row.year] == row.run_id
        ]

    # ---- management pack ------------------------------------------------

    def _cmc_pack_data(self, month=None):
        """Data of the monthly management pack, from the run rows in one read each (AD-114)."""
        self.ensure_one()
        month = month_start(month or self.date_to)
        rank = {key: index for index, (key, _label) in enumerate(PERSPECTIVES)}
        labels = dict(PERSPECTIVES)
        values = self.env["cmc.kpi.value"].search([("run_id", "=", self.id), ("date", "=", month)])
        kpis = [{
            "kpi": row.kpi_id, "value": row.value, "computable": row.computable, "target": row.target,
            "value_display": row.value_display, "target_display": row.target_display,
            "status": row.status, "trend": row.trend, "score": row.score,
        } for row in values.sorted(lambda row: (rank[row.perspective], row.kpi_id.sequence, row.code))]
        results = self.env["cmc.kpi.scorecard.result"].search([("run_id", "=", self.id), ("date", "=", month)])
        scorecards = []
        for scorecard, rows in results.grouped("scorecard_id").items():
            overall = rows.filtered(lambda row: not row.perspective)
            scorecards.append({
                "scorecard": scorecard,
                "overall": overall.score if overall.computable else None,
                "perspectives": [{
                    "perspective": row.perspective, "label": labels[row.perspective], "weight": row.weight,
                    "score": row.score, "computable": row.computable,
                } for row in rows.filtered("perspective").sorted(lambda row: rank[row.perspective])],
            })
        bva = self.env["cmc.kpi.run.bva"].search([("run_id", "=", self.id), ("date", "=", month)])
        ratios = self.env["cmc.kpi.run.ratio"].search([("run_id", "=", self.id), ("year", "=", month.year)])
        # why: the pack prints percent ratios like the screens (RN-008); a company ratio overrides the shared one
        ratio_units = {
            ratio.code: ratio.unit for ratio in self.env["cmc.fin.ratio"].search(
                [("code", "in", ratios.mapped("code")), ("company_id", "in", [False, self.company_id.id])],
            ).sorted(lambda ratio: bool(ratio.company_id))
        }
        return {
            "month": month, "kpis": kpis, "scorecards": scorecards,
            "bva": [{"center_id": row.center_id} | {key: row[key] for key in BVA_KEYS} for row in bva],
            "ratios": [{
                "code": row.code, "name": row.name, "value": row.value, "status": row.status,
                "value_display": format_figure(self.env, row.value, ratio_units.get(row.code) == "percent"),
            } for row in ratios],
        }
