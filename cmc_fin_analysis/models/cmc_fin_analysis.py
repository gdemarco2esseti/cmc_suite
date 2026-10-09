import math
import operator
from collections import Counter, defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_is_zero
from odoo.tools.safe_eval import safe_eval

from .cmc_fin_utils import split_codes

THRESHOLD_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def _evaluate(expression, namespace):
    """Value of a safe_eval expression, None when it cannot be computed (unknown name, division by zero)."""
    try:
        return float(safe_eval(expression, dict(namespace)))
    except (ValueError, ZeroDivisionError, TypeError):
        return None


class _Mapper:
    """Account -> scheme line resolution of one run, in memory (AD-114: zero queries per account)."""

    def __init__(self, run, schemes, account_ids):
        company = run.company_id
        accounts = run.env["account.account"].sudo().with_company(company).browse(account_ids)  # why sudo: CMC users have no accounting rights
        self.infos = {row["id"]: row for row in accounts.read(["code", "account_type", "include_initial_balance"])}
        self.rules = {
            scheme.id: scheme.rule_ids.filtered(lambda rule: not rule.company_id or rule.company_id == company).sorted(lambda rule: (rule.sequence, rule.id))
            for scheme in schemes
        }
        self.behavior_rules = (
            run.env["cmc.classification.rule"]._rules_for_company(company).filtered(
                lambda rule: rule.cmc_behavior and not (rule.product_category_id or rule.partner_id or rule.center_id)
            )
            if any(rule.cmc_behavior for rules in self.rules.values() for rule in rules)
            else []
        )
        self.cache = {}

    def is_balance_sheet(self, account_id):
        return self.infos[account_id]["include_initial_balance"] and self.infos[account_id]["account_type"] != "off_balance"

    def is_income_statement(self, account_id):
        return not self.infos[account_id]["include_initial_balance"] and self.infos[account_id]["account_type"] != "off_balance"

    def behavior(self, account_id):
        code = self.infos[account_id]["code"] or ""
        for rule in self.behavior_rules:
            if rule.general_account_id and rule.general_account_id.id != account_id:
                continue
            if rule.account_code_prefix and not code.startswith(rule.account_code_prefix):
                continue
            return rule.cmc_behavior
        return False

    def line(self, scheme, account_id):
        key = (scheme.id, account_id)
        if key not in self.cache:
            info = self.infos[account_id]
            code = info["code"] or ""
            behavior = self.behavior(account_id)
            self.cache[key] = next(
                (
                    rule.line_id
                    for rule in self.rules[scheme.id]
                    if (not rule.account_id or rule.account_id.id == account_id)
                    and (not rule.account_code_prefix or code.startswith(rule.account_code_prefix))
                    and (not rule.account_type or rule.account_type == info["account_type"])
                    and (not rule.cmc_behavior or rule.cmc_behavior == behavior)
                ),
                None,
            )
        return self.cache[key]


class CmcFinAnalysis(models.Model):
    _name = "cmc.fin.analysis"
    _inherit = ["cmc.run.mixin"]
    _description = "Financial Statement Analysis"
    _order = "year_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("Financial analysis"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    year_from = fields.Integer(required=True, default=lambda self: fields.Date.today().year - 2, aggregator=False)
    year_to = fields.Integer(required=True, default=lambda self: fields.Date.today().year, aggregator=False)
    scheme_ids = fields.Many2many("cmc.fin.scheme", default=lambda self: self._default_scheme_ids())
    reduced_mode = fields.Boolean(help="Abbreviated statements: ratios needing detail are flagged not computable.")
    ateco_code = fields.Char(help="Sector (ATECO) of the benchmarks shown next to the ratios.")
    value_ids = fields.One2many("cmc.fin.analysis.value", "run_id")
    ratio_value_ids = fields.One2many("cmc.fin.ratio.value", "run_id")

    _cmc_input_fields = ("company_id", "year_from", "year_to", "scheme_ids", "reduced_mode", "ateco_code")
    _cmc_result_fields = ("value_ids", "ratio_value_ids")

    def _default_scheme_ids(self):
        company_domain = Domain("company_id", "=", False) | Domain("company_id", "parent_of", self.env.company.id)
        return self.env["cmc.fin.scheme"].search(Domain("active", "=", True) & company_domain)

    @api.constrains("year_from", "year_to")
    def _check_years(self):
        for run in self:
            if run.year_to < run.year_from:
                raise ValidationError(self.env._("The last year cannot precede the first year."))

    # ---- compute --------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        company = self.company_id
        years = list(range(self.year_from, self.year_to + 1))
        schemes = self.scheme_ids.sorted(lambda scheme: (scheme.sequence, scheme.id))
        ratios = self._fin_ratios()
        inputs = self._fin_inputs(years)
        self._fin_check_codes(schemes, ratios, inputs)
        breaks = self._fin_breaks(years)
        balances = self._fin_balances(years)
        mapper = _Mapper(self, schemes, {acc for accounts, _source in balances.values() for acc in accounts})
        overrides = self._fin_overrides(schemes, years)
        benchmarks = self._fin_benchmarks(years)
        value_vals, ratio_vals = [], []
        prev = {}
        for year in years:
            accounts, source = balances[year]
            namespace = {f"prev_{code}": value for code, value in prev.items()}
            namespace.update(inputs.get(year, {}))
            result = self._fin_result(mapper, accounts, source)
            entries = []  # (scheme, line, value) in presentation order
            for scheme in schemes:
                if scheme.scheme_type == "cash_flow":
                    continue
                natural = self._fin_natural(scheme, mapper, accounts, overrides.get((scheme.id, year), ()), result)
                for line in scheme.line_ids.filtered(lambda line: line.line_type == "account"):
                    entries.append((scheme, line, line.sign * natural[line.id]))
                    namespace[line.code] = entries[-1][2]
            for scheme in schemes:
                for line in scheme.line_ids.filtered(lambda line: line.line_type == "formula"):
                    value = _evaluate(line.formula, namespace)
                    if value is not None:
                        entries.append((scheme, line, value))
                        namespace[line.code] = value
            for scheme, line, value in entries:
                base = namespace.get(scheme.base_code) if scheme.base_code else None
                before = prev.get(line.code)
                computable = before is not None and not float_is_zero(before, precision_digits=6)
                value_vals.append({
                    "run_id": self.id, "company_id": company.id, "year": year, "scheme_id": scheme.id, "line_id": line.id,
                    "code": line.code, "sequence": line.sequence, "value": value, "source": source,
                    "common_size_pct": value / base * 100 if base else 0.0,
                    "yoy_computable": computable, "yoy_pct": (value - before) / before * 100 if computable else 0.0,
                    "comparability_break": line.code in breaks.get(year, ()),
                })
            prev = {line.code: value for _scheme, line, value in entries}
            ratio_vals += self._fin_ratio_vals(ratios, year, namespace, benchmarks)
        # why sudo: results are written by the engine only, under the run sentinel of action_compute
        self.env["cmc.fin.analysis.value"].sudo().create(value_vals)
        self.env["cmc.fin.ratio.value"].sudo().create(ratio_vals)

    def _fin_ratios(self):
        company_domain = Domain("company_id", "=", False) | Domain("company_id", "parent_of", self.company_id.id)
        return self.env["cmc.fin.ratio"].search(company_domain)

    def _fin_check_codes(self, schemes, ratios, inputs):
        counts = Counter(line.code for scheme in schemes for line in scheme.line_ids) + Counter(ratios.mapped("code"))
        counts += Counter(code for year_inputs in inputs.values() for code in year_inputs)
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            raise UserError(self.env._("These codes are defined more than once: %s", ", ".join(duplicates)))

    def _fin_balance_date(self, year):
        # why sudo: with account_accountant the fiscal-year lookup needs accounting rights a CMC user lacks
        company = self.company_id.sudo()
        dates = company.compute_fiscalyear_dates(fields.Date.to_date(f"{year}-12-31"))
        if dates["date_to"].year > year:
            dates = company.compute_fiscalyear_dates(dates["date_from"] - timedelta(days=1))
        return dates["date_to"]

    def _fin_balances(self, years):
        """{year: ({account_id: balance}, source)}: imported history wins over the GL (AD-116)."""
        company = self.company_id
        rounding = company.currency_id.rounding
        history = defaultdict(dict)
        for year, account, balance in self.env["cmc.fin.history"]._read_group(
            [("company_id", "=", company.id), ("year", "in", years)], ["year", "account_id"], ["balance:sum"]
        ):
            history[year][account.id] = balance
        Line = self.env["account.move.line"]
        balances = {}
        for year in years:
            if year in history:
                if not float_is_zero(sum(history[year].values()), precision_rounding=rounding):
                    raise UserError(self.env._("The balances imported for %s do not sum to zero.", year))
                accounts, source = history[year], "history"
            else:
                # why sudo on the company record only: the fiscal-year lookup needs accounting rights; the reader gate stays on the user env
                balances_at = Line._cmc_gl_balances_at(company.sudo(), self._fin_balance_date(year))
                accounts = {account.id: balance for account, balance in balances_at.items()}
                source = "gl"
            balances[year] = ({acc: bal for acc, bal in accounts.items() if not float_is_zero(bal, precision_rounding=rounding)}, source)
        return balances

    def _fin_result(self, mapper, accounts, source):
        """Natural balance of the result not yet closed to equity (cumulative P&L), from the same balances."""
        if source == "history":
            return sum(bal for acc, bal in accounts.items() if mapper.is_income_statement(acc))
        return -sum(bal for acc, bal in accounts.items() if mapper.is_balance_sheet(acc))

    def _fin_natural(self, scheme, mapper, accounts, overrides, result):
        is_bs = scheme.scheme_type == "balance_sheet"
        natural = defaultdict(float)
        placed, unmapped = {}, set()
        for acc, balance in accounts.items():
            if not (mapper.is_balance_sheet(acc) if is_bs else mapper.is_income_statement(acc)):
                continue
            line = mapper.line(scheme, acc)
            if line:
                natural[line.id] += balance
                placed[acc] = line
            else:
                unmapped.add(mapper.infos[acc]["code"])
        if unmapped:
            raise UserError(self.env._("No mapping rule of scheme %(scheme)s places the accounts: %(accounts)s", scheme=scheme.display_name, accounts=", ".join(sorted(unmapped))))
        remaining = dict(accounts)
        for override in overrides:
            acc = override.account_id.id
            if acc not in placed:
                continue
            moved = math.copysign(min(abs(override.amount) or math.inf, abs(remaining[acc])), remaining[acc])
            remaining[acc] -= moved
            natural[placed[acc].id] -= moved
            natural[override.line_id.id] += moved
        if is_bs:
            for line in scheme.line_ids.filtered("result_line"):
                natural[line.id] += result
        return natural

    def _fin_overrides(self, schemes, years):
        overrides = self.env["cmc.fin.override"].search([
            ("company_id", "=", self.company_id.id), ("scheme_id", "in", schemes.ids), ("year", "in", years),
        ], order="id")
        grouped = defaultdict(list)
        for override in overrides:
            grouped[(override.scheme_id.id, override.year)].append(override)
        return grouped

    def _fin_inputs(self, years):
        grouped = defaultdict(dict)
        for row in self.env["cmc.fin.input"].search_read([("company_id", "=", self.company_id.id), ("year", "in", years)], ["year", "code", "value"]):
            grouped[row["year"]][row["code"]] = row["value"]
        return grouped

    def _fin_breaks(self, years):
        """{year: {line code}} of the valuation-criteria changes of the run company."""
        breaks = defaultdict(set)
        for row in self.env["cmc.fin.criteria.change"].search_read([("company_id", "=", self.company_id.id), ("year", "in", years)], ["year", "line_codes"]):
            breaks[row["year"]].update(split_codes(row["line_codes"]))
        return breaks

    def _fin_benchmarks(self, years):
        if not self.ateco_code:
            return {}
        company_domain = Domain("company_id", "=", False) | Domain("company_id", "parent_of", self.company_id.id)
        rows = self.env["cmc.fin.benchmark"].search(Domain("ateco_code", "=", self.ateco_code) & Domain("year", "in", years) & company_domain)
        # company rows override the shared ones
        return {(row.year, row.ratio_id.id): row.value for row in rows.sorted(lambda row: bool(row.company_id))}

    def _fin_ratio_vals(self, ratios, year, namespace, benchmarks):
        vals = []
        for ratio in ratios:
            value = None if self.reduced_mode and ratio.full_only else _evaluate(ratio.expression, namespace)
            status = "not_computable"
            if value is not None:
                namespace[ratio.code] = value
                alert = ratio.threshold_op and not THRESHOLD_OPS[ratio.threshold_op](value, ratio.threshold)
                status = "alert" if alert else "ok"
            benchmark = benchmarks.get((year, ratio.id))
            vals.append({
                "run_id": self.id, "company_id": self.company_id.id, "year": year, "ratio_id": ratio.id, "code": ratio.code,
                "value": value or 0.0, "status": status, "benchmark": benchmark or 0.0, "has_benchmark": benchmark is not None,
            })
        return vals

    # ---- report ---------------------------------------------------------

    def _fin_report_data(self):
        """Data of the QWeb report, built from the run's own rows in one read each (AD-114)."""
        self.ensure_one()
        values = defaultdict(lambda: defaultdict(dict))  # code -> field -> {year: x}
        years = set()
        for row in self.env["cmc.fin.analysis.value"].search_read([("run_id", "=", self.id)], ["code", "year", "value", "common_size_pct", "comparability_break"]):
            years.add(row["year"])
            values[row["code"]]["values"][row["year"]] = row["value"]
            values[row["code"]]["common_size"][row["year"]] = row["common_size_pct"]
            if row["comparability_break"]:
                values[row["code"]]["breaks"][row["year"]] = True
        schemes = [{
            "scheme": scheme,
            "lines": [{
                "code": line.code, "name": line.name, "line_type": line.line_type,
                "values": values[line.code]["values"], "common_size": values[line.code]["common_size"],
                "breaks": sorted(values[line.code]["breaks"]),
            } for line in scheme.line_ids.sorted(lambda line: (line.sequence, line.id))],
        } for scheme in self.scheme_ids.sorted(lambda scheme: (scheme.sequence, scheme.id))]
        rows = self.env["cmc.fin.ratio.value"].search_read([("run_id", "=", self.id)], ["ratio_id", "year", "value", "status", "benchmark", "has_benchmark"])
        by_ratio = defaultdict(dict)
        for row in rows:
            years.add(row["year"])
            by_ratio[row["ratio_id"][0]][row["year"]] = {
                "value": row["value"], "status": row["status"], "benchmark": row["benchmark"] if row["has_benchmark"] else None,
            }
        Ratio = self.env["cmc.fin.ratio"]
        labels = dict(Ratio._fields["area"]._description_selection(self.env))
        by_area = defaultdict(list)
        for ratio in Ratio.browse(by_ratio).sorted(lambda ratio: (ratio.sequence, ratio.id)):
            by_area[ratio.area].append({"code": ratio.code, "name": ratio.name, "unit": ratio.unit, "values": by_ratio[ratio.id]})
        ratios = [{"area": area, "area_label": label, "rows": by_area[area]} for area, label in labels.items() if by_area[area]]
        return {"years": sorted(years), "schemes": schemes, "ratios": ratios}


class CmcFinAnalysisValue(models.Model):
    _name = "cmc.fin.analysis.value"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Financial Analysis Value"
    _order = "scheme_id, sequence, year"

    run_id = fields.Many2one("cmc.fin.analysis", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    year = fields.Integer(required=True, aggregator=False)
    scheme_id = fields.Many2one("cmc.fin.scheme", required=True, ondelete="cascade")
    line_id = fields.Many2one("cmc.fin.scheme.line", required=True, ondelete="cascade")
    code = fields.Char(required=True)
    name = fields.Char(related="line_id.name")
    sequence = fields.Integer()
    value = fields.Float(aggregator="sum")
    source = fields.Selection([("gl", "General ledger"), ("history", "Imported history")])
    common_size_pct = fields.Float(aggregator="avg")
    yoy_pct = fields.Float(aggregator="avg")
    yoy_computable = fields.Boolean()
    comparability_break = fields.Boolean(help="A valuation-criteria change makes this year, and its YoY, not comparable.")


class CmcFinRatioValue(models.Model):
    _name = "cmc.fin.ratio.value"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Financial Analysis Ratio Value"
    _order = "ratio_id, year"

    run_id = fields.Many2one("cmc.fin.analysis", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    year = fields.Integer(required=True, aggregator=False)
    ratio_id = fields.Many2one("cmc.fin.ratio", required=True, ondelete="cascade")
    code = fields.Char(required=True)
    name = fields.Char(related="ratio_id.name")
    area = fields.Selection(related="ratio_id.area", store=True)
    unit = fields.Selection(related="ratio_id.unit")
    threshold_op = fields.Selection(related="ratio_id.threshold_op")
    threshold = fields.Float(related="ratio_id.threshold")
    value = fields.Float(aggregator="avg")
    status = fields.Selection([("ok", "OK"), ("alert", "Alert"), ("not_computable", "Not computable")])
    benchmark = fields.Float()
    has_benchmark = fields.Boolean()
