from collections import defaultdict
from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_utils import month_range

from .cmc_standard_mixin import PRECISE


def _pair():
    return [0.0, 0.0]


class CmcStandardVarianceRun(models.Model):
    _name = "cmc.standard.variance.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Standard Cost Variance Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = ("company_id", "version_id", "date_from", "date_to")
    _cmc_result_fields = ("line_ids", "absorption_ids")

    name = fields.Char(required=True, default=lambda self: self.env._("Standard cost variances"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    version_id = fields.Many2one(
        "cmc.plan.version", required=True, check_company=True, domain="[('version_type', '=', 'standard'), ('state', '=', 'done')]",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    std_amount_total = fields.Monetary(readonly=True, copy=False)
    actual_amount_total = fields.Monetary(readonly=True, copy=False)
    variance_total = fields.Monetary(readonly=True, copy=False, help="Actual minus standard, natural sign: positive is favourable.")
    skipped_count = fields.Integer(readonly=True, copy=False, help="Manufacturing orders of the period whose product has no card.")
    line_ids = fields.One2many("cmc.standard.variance.line", "run_id")
    absorption_ids = fields.One2many("cmc.standard.absorption.line", "run_id")

    @api.constrains("version_id", "date_from", "date_to")
    def _check_period(self):
        for run in self:
            version = run.version_id
            if version.version_type != "standard":
                raise ValidationError(self.env._("%s is not a standard version.", version.display_name))
            if not version.date_from <= run.date_from <= run.date_to <= version.date_to:
                raise ValidationError(self.env._("The period must be ordered and inside the span of %s.", version.display_name))

    def _cmc_reset_values(self):
        return dict.fromkeys(("std_amount_total", "actual_amount_total", "variance_total", "skipped_count"), 0)

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        version = self.version_id
        # raises unless the version is approved; locks it against a reset while this run is computed
        self._cmc_record_usage(version)
        mos = self._cmc_std_productions()
        actual = self._cmc_std_actuals(mos)
        standard = self._cmc_std_card_lines(version)
        output = self._cmc_std_output(mos)
        carded = mos.filtered(lambda mo: mo.product_id.id in standard)
        vals_list = []
        for mo in carded:
            for line_type in ("material", "labour"):
                vals_list += self._cmc_std_vals(mo, output.get(mo.id, 0.0), line_type, standard[mo.product_id.id][line_type], actual[line_type].get(mo.id, {}))
        hours = defaultdict(lambda: {"actual": 0.0, "standard": 0.0})
        for centers in actual["labour"].values():
            for center, (qty, _amount) in centers.items():
                hours[center]["actual"] += qty
        for vals in vals_list:
            if vals["line_type"] == "labour":
                hours[vals["center_id"]]["standard"] += vals["std_qty"]
        self.env["cmc.standard.variance.line"].create(vals_list)
        std_total = sum(vals["std_amount"] for vals in vals_list)
        actual_total = sum(vals["actual_amount"] for vals in vals_list)
        self.write({
            "std_amount_total": std_total, "actual_amount_total": actual_total, "variance_total": actual_total - std_total,
            "skipped_count": len(mos) - len(carded),
        })
        self.env["cmc.standard.absorption.line"].create(self._cmc_std_absorption_vals(version, hours))

    def _cmc_std_productions(self):
        return self.env["mrp.production"].search([
            ("state", "=", "done"), ("company_id", "=", self.company_id.id), ("cmc_center_id", "!=", False),
            ("date_finished", ">=", datetime.combine(self.date_from, time.min)),
            ("date_finished", "<=", datetime.combine(self.date_to, time.max)),
        ])

    def _cmc_std_output(self, mos):
        """{mo id: done finished quantity of the MO product, in the product UoM, net of unbuilds}; same population as the produced-qty feeder."""
        output = defaultdict(float)
        profiles = [profile for profile in self.env["cmc.measure.quantity"]._cmc_feeder_profiles() if profile["key"] == "mrp_produced_qty"]
        for profile in profiles:
            # the owner MO sits behind the center path: production_id.cmc_center_id / unbuild_id.mo_id.cmc_center_id
            owner = profile["center_field"].removesuffix(".cmc_center_id")
            head, _dot, tail = owner.partition(".")
            domain = Domain(profile["domain"]) & Domain(owner, "in", mos.ids)
            for record, product, uom, quantity in self.env["stock.move"]._read_group(domain, [head, "product_id", "product_uom"], ["quantity:sum"]):
                mo = record.mapped(tail) if tail else record
                if product == mo.product_id:
                    output[mo.id] += profile["sign"] * uom._compute_quantity(quantity, product.uom_id, round=False)
        return output

    def _cmc_std_card_lines(self, version):
        """{product id: {line type: {key: [qty, amount]}}} standard per unit, key = component (material) or center (labour)."""
        products = {card.id: card.product_id.id for card in self.env["cmc.standard.card"].search_fetch([("version_id", "=", version.id)], ["product_id"])}
        standard = {product: {"material": defaultdict(_pair), "labour": defaultdict(_pair)} for product in products.values()}
        for line in self.env["cmc.standard.card.line"].search_fetch(
            [("version_id", "=", version.id), ("line_type", "in", ("material", "labour"))],
            ["card_id", "line_type", "product_id", "center_id", "quantity", "amount"],
        ):
            key = line.product_id.id if line.line_type == "material" else line.center_id.id
            entry = standard[products[line.card_id.id]][line.line_type][key]
            entry[0] += line.quantity
            entry[1] += line.amount
        return standard

    def _cmc_std_actuals(self, mos):
        """{"material": {mo id: {component id: [qty, amount]}}, "labour": {mo id: {center id: [hours, amount]}}}, natural sign."""
        actual = {"material": defaultdict(lambda: defaultdict(_pair)), "labour": defaultdict(lambda: defaultdict(_pair))}
        moves = {
            move.id: (move.raw_material_production_id.id, move.product_id.id)
            for move in self.env["stock.move"].search_fetch(
                [("raw_material_production_id", "in", mos.ids), ("state", "=", "done")], ["raw_material_production_id", "product_id"]
            )
        }
        # why sudo: a certified actual does not depend on the analytic line rules of the caller; the MO set is the scope
        Line = self.env["account.analytic.line"].sudo()
        for move, quantity, amount in Line._read_group(
            Domain("cmc_mrp_move_id", "in", list(moves)), ["cmc_mrp_move_id"], ["unit_amount:sum", "amount:sum"]
        ):
            mo_id, product_id = moves[move.id]
            entry = actual["material"][mo_id][product_id]
            entry[0] += quantity
            entry[1] += amount
        orders = {}
        for workorder in self.env["mrp.workorder"].sudo().search_fetch(
            [("production_id", "in", mos.ids)], ["production_id", "wc_analytic_account_line_ids"]
        ):
            orders.update(dict.fromkeys(workorder.wc_analytic_account_line_ids.ids, workorder.production_id.id))
        for line in Line.search_fetch([("id", "in", list(orders))], ["account_id", "unit_amount", "amount"]):
            entry = actual["labour"][orders[line.id]][line.account_id.id]
            entry[0] += line.unit_amount
            entry[1] += line.amount
        return actual

    def _cmc_std_vals(self, mo, output, line_type, standard, actual):
        """Result rows of one MO and line type; the material rows carry the mix / yield split of the quantity variance."""
        key_field = "component_id" if line_type == "material" else "center_id"
        vals_list = []
        for key in set(standard) | set(actual):
            unit_qty, unit_amount = standard.get(key, (0.0, 0.0))
            std_qty = unit_qty * output
            std_price = unit_amount / unit_qty if unit_qty else 0.0
            actual_qty, actual_amount = actual.get(key, (0.0, 0.0))
            vals_list.append({
                "run_id": self.id, "production_id": mo.id, "product_id": mo.product_id.id, "line_type": line_type,
                "output_qty": output, key_field: key,
                "std_qty": std_qty, "actual_qty": actual_qty, "std_price": std_price,
                "actual_price": -actual_amount / actual_qty if actual_qty else 0.0,
                "std_amount": -std_price * std_qty, "actual_amount": actual_amount,
                # amount-based, so price + quantity = actual - standard also when a value is posted without quantity
                "price_variance": std_price * actual_qty + actual_amount,
                "quantity_variance": (std_qty - actual_qty) * std_price,
                "mix_variance": 0.0, "yield_variance": 0.0,
                "total_variance": actual_amount + std_price * std_qty,
            })
        if line_type == "material":
            std_total = sum(vals["std_qty"] for vals in vals_list)
            actual_total = sum(vals["actual_qty"] for vals in vals_list)
            for vals in vals_list:
                share = vals["std_qty"] / std_total if std_total else 0.0
                vals["mix_variance"] = (share * actual_total - vals["actual_qty"]) * vals["std_price"]
                vals["yield_variance"] = (vals["std_qty"] - share * actual_total) * vals["std_price"]
        return vals_list

    def _cmc_std_indirect(self, centers):
        """{center id: {"fixed": amount, "variable": amount}} indirect analytic cost of the period, natural sign."""
        Line = self.env["account.analytic.line"].sudo()  # why sudo: same as the actuals above
        base = Domain([
            ("company_id", "=", self.company_id.id), ("cmc_excluded", "=", False), ("cmc_directness", "=", "indirect"),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
        ])
        indirect = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0})
        for plan, accounts in centers.grouped("plan_id").items():
            column = plan._column_name()
            for center, behavior, amount in Line._read_group(base & Domain(column, "in", accounts.ids), [column, "cmc_behavior"], ["amount:sum"]):
                indirect[center.id]["variable" if behavior == "variable" else "fixed"] += amount
        return indirect

    def _cmc_std_absorption_vals(self, version, hours):
        rates = version.std_rate_ids.filtered(lambda line: line.rate_kind == "overhead")
        if not rates:
            return []
        # the yearly budget and capacity of the rate table apply to the months of the run
        share = len(month_range(self.date_from, self.date_to)) / len(version._cmc_months())
        indirect = self._cmc_std_indirect(rates.center_id)
        vals_list = []
        for rate in rates:
            center = rate.center_id.id
            worked = hours[center]["actual"]
            standard = hours[center]["standard"]
            actual_fixed, actual_variable = indirect[center]["fixed"], indirect[center]["variable"]
            budget_fixed = -rate.budget_fixed * share
            capacity = rate.capacity_qty * share
            actual_amount = actual_fixed + actual_variable
            applied_actual = -rate.rate * worked
            applied_standard = -rate.rate * standard
            vals_list.append({
                "run_id": self.id, "center_id": center, "driver_id": rate.driver_id.id,
                "fixed_rate": rate.fixed_rate, "variable_rate": rate.variable_rate,
                "actual_qty": worked, "standard_qty": standard, "capacity_qty": capacity, "budget_fixed": budget_fixed,
                "actual_fixed": actual_fixed, "actual_variable": actual_variable, "actual_amount": actual_amount,
                "applied_actual": applied_actual, "applied_standard": applied_standard,
                "over_under_absorbed": actual_amount - applied_actual,
                "spending_variable": actual_variable + rate.variable_rate * worked,
                "efficiency_variable": (standard - worked) * rate.variable_rate,
                "spending_fixed": actual_fixed - budget_fixed,
                "volume_variance": (standard - capacity) * rate.fixed_rate,
            })
        return vals_list


class CmcStandardVarianceLine(models.Model):
    _name = "cmc.standard.variance.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Standard Cost Variance Result"
    _order = "run_id, production_id, line_type, id"

    run_id = fields.Many2one("cmc.standard.variance.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    line_type = fields.Selection([("material", "Material"), ("labour", "Labour")], required=True)
    production_id = fields.Many2one("mrp.production", string="Manufacturing Order", ondelete="restrict", index=True)
    product_id = fields.Many2one("product.product", string="Finished Product", ondelete="restrict")
    component_id = fields.Many2one("product.product", string="Component", ondelete="restrict")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", ondelete="restrict")
    output_qty = fields.Float(digits=PRECISE)
    std_qty = fields.Float(digits=PRECISE)
    actual_qty = fields.Float(digits=PRECISE)
    std_price = fields.Float(digits=PRECISE)
    actual_price = fields.Float(digits=PRECISE)
    std_amount = fields.Monetary()
    actual_amount = fields.Monetary()
    price_variance = fields.Monetary(help="Material price variance; for labour the rate variance.")
    quantity_variance = fields.Monetary(help="Material usage variance; for labour the efficiency variance.")
    mix_variance = fields.Monetary()
    yield_variance = fields.Monetary()
    total_variance = fields.Monetary(help="Actual minus standard, natural sign: positive is favourable.")


class CmcStandardAbsorptionLine(models.Model):
    _name = "cmc.standard.absorption.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Overhead Absorption Result"
    _order = "run_id, center_id"

    run_id = fields.Many2one("cmc.standard.variance.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", ondelete="restrict")
    driver_id = fields.Many2one("cmc.cost.driver", ondelete="restrict")
    fixed_rate = fields.Float(digits=PRECISE)
    variable_rate = fields.Float(digits=PRECISE)
    actual_qty = fields.Float(digits=PRECISE, help="Actual base (AH).")
    standard_qty = fields.Float(digits=PRECISE, help="Standard base for the actual output (SH).")
    capacity_qty = fields.Float(digits=PRECISE, help="Capacity of the period (D).")
    budget_fixed = fields.Monetary()
    actual_fixed = fields.Monetary()
    actual_variable = fields.Monetary()
    actual_amount = fields.Monetary()
    applied_actual = fields.Monetary(help="Predetermined rate x actual base.")
    applied_standard = fields.Monetary(help="Predetermined rate x standard base.")
    over_under_absorbed = fields.Monetary(help="Actual minus applied: positive is over-absorbed.")
    spending_variable = fields.Monetary()
    efficiency_variable = fields.Monetary()
    spending_fixed = fields.Monetary()
    volume_variance = fields.Monetary()
