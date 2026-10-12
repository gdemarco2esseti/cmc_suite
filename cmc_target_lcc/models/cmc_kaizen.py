from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models.cmc_utils import month_range, month_start


class CmcKaizenTarget(models.Model):
    _name = "cmc.kaizen.target"
    _inherit = ["cmc.tlc.run.mixin"]
    _description = "Kaizen Target"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    basis = fields.Selection([("unit", "Unit cost"), ("flexible", "Flexible budget")], required=True, default="unit")
    reduction_pct = fields.Float(string="Reduction %", digits=(16, 2))
    product_id = fields.Many2one("product.product", check_company=True)
    unit_driver_id = fields.Many2one("cmc.cost.driver", string="Unit Driver")
    base_date = fields.Date(help="Month of the baseline actual unit cost.")
    date_from = fields.Date(help="First month tracked.")
    date_to = fields.Date(help="Last month tracked.")
    fixed_budget = fields.Monetary()
    variable_budget = fields.Monetary(help="Flexible variable budget at the actual mix.")
    actual_cost = fields.Monetary()
    line_ids = fields.One2many("cmc.kaizen.target.line", "target_id", string="Tracking", copy=False)
    base_unit_cost = fields.Monetary(readonly=True, copy=False)
    target_unit_cost = fields.Monetary(readonly=True, copy=False)
    target_total = fields.Monetary(readonly=True, copy=False)
    achieved_pct = fields.Float(string="Achieved %", readonly=True, copy=False, digits=(16, 2))
    variance = fields.Monetary(readonly=True, copy=False, help="Target - actual: positive is favourable.")
    target_met = fields.Boolean(readonly=True, copy=False)

    _cmc_input_fields = ( "company_id", "basis", "reduction_pct", "product_id", "unit_driver_id", "base_date", "date_from", "date_to",
        "fixed_budget", "variable_budget", "actual_cost",
    )
    _cmc_result_fields = ("line_ids",)
    _cmc_output_fields = ("base_unit_cost", "target_unit_cost", "target_total", "achieved_pct", "variance", "target_met")

    @api.constrains("basis", "product_id", "unit_driver_id", "base_date", "date_from", "date_to")
    def _check_unit_basis(self):
        for kaizen in self.filtered(lambda k: k.basis == "unit"):
            if not (kaizen.product_id and kaizen.unit_driver_id and kaizen.base_date and kaizen.date_from and kaizen.date_to):
                raise ValidationError(self.env._("A unit kaizen needs a product, a unit driver, the base month and the tracked months."))
            if kaizen.date_to < kaizen.date_from:
                raise ValidationError(self.env._("The last tracked month is before the first."))

    def _cmc_compute(self):
        for kaizen in self:
            if kaizen.basis == "flexible":
                kaizen._tlc_compute_flexible()
            else:
                kaizen._tlc_compute_unit()

    def _tlc_compute_flexible(self):
        currency = self.currency_id
        target = currency.round((1 - self.reduction_pct / 100) * (self.fixed_budget + self.variable_budget))
        variance = currency.round(target - self.actual_cost)
        self.write({"target_total": target, "variance": variance, "target_met": variance >= 0})

    def _tlc_compute_unit(self):
        currency = self.currency_id
        base_month = month_start(self.base_date)
        months = month_range(self.date_from, self.date_to)
        obj = self.env["cmc.cost.object"]._cmc_ensure_objects(self.product_id, company=self.company_id)
        rates = self._tlc_rates({(obj._name, obj.id, self.unit_driver_id.id, month) for month in [base_month, *months]})
        base_rate = rates.get((obj._name, obj.id, self.unit_driver_id.id, base_month))
        if not base_rate:
            raise UserError(self.env._("No actual unit cost published for %(product)s in %(month)s.", product=self.product_id.display_name, month=base_month))
        base = currency.round(base_rate.value)
        target = currency.round(base * (1 - self.reduction_pct / 100))
        vals_list = []
        for month in months:
            rate = rates.get((obj._name, obj.id, self.unit_driver_id.id, month))
            if rate:
                actual = currency.round(rate.value)
                vals_list.append({
                    "target_id": self.id, "date": month, "actual_unit_cost": actual,
                    "achieved_pct": round((base - actual) / base * 100, 2) if base else 0.0,
                    "variance": currency.round(target - actual), "met": actual <= target,
                })
        # why sudo: users read the tracking, the engine writes it
        self.env["cmc.kaizen.target.line"].sudo().create(vals_list)
        last = vals_list[-1] if vals_list else {}
        self.write({
            "base_unit_cost": base, "target_unit_cost": target, "achieved_pct": last.get("achieved_pct", 0.0),
            "variance": last.get("variance", 0.0), "target_met": last.get("met", False),
        })


class CmcKaizenTargetLine(models.Model):
    _name = "cmc.kaizen.target.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Kaizen Tracking Month"
    _order = "date, id"

    target_id = fields.Many2one("cmc.kaizen.target", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="target_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="target_id.currency_id")
    date = fields.Date(required=True)
    actual_unit_cost = fields.Monetary()
    achieved_pct = fields.Float(string="Achieved %", digits=(16, 2))
    variance = fields.Monetary(help="Target - actual: positive is favourable.")
    met = fields.Boolean()
