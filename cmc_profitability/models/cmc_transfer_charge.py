from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_round

from odoo.addons.cmc_base.models.cmc_utils import month_start, spread

CRITERIA = [
    ("marginal", "Marginal cost"),
    ("full", "Full cost"),
    ("cost_plus", "Cost plus"),
    ("market", "Market price"),
    ("negotiated", "Negotiated price"),
    ("abc", "ABC activity rate"),
]


class CmcTransferCharge(models.Model):
    _name = "cmc.transfer.charge"
    _inherit = ["cmc.run.mixin"]
    _description = "Transfer Price Chargeback"
    _order = "date_to desc, id desc"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    provider_id = fields.Many2one("account.analytic.account", string="Provider Center", required=True, check_company=True)
    driver_id = fields.Many2one("cmc.cost.driver", string="Service Unit", required=True, check_company=True)
    criterion = fields.Selection(CRITERIA, required=True, default="full")
    markup_percent = fields.Float()
    market_price = fields.Float(digits=(16, 6))
    negotiated_price = fields.Float(digits=(16, 6))
    abc_activity_id = fields.Many2one("cmc.abc.activity", string="ABC Activity", check_company=True)
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    line_ids = fields.One2many("cmc.transfer.charge.line", "charge_id", string="Receivers", copy=True)

    provider_cost = fields.Monetary(readonly=True, copy=False)
    provider_variable_cost = fields.Monetary(readonly=True, copy=False)
    service_units = fields.Float(readonly=True, copy=False)
    unit_cost = fields.Float(readonly=True, copy=False, digits=(16, 6))
    unit_variable_cost = fields.Float(readonly=True, copy=False, digits=(16, 6))
    transfer_price = fields.Float(readonly=True, copy=False, digits=(16, 6))
    internal_margin_total = fields.Monetary(readonly=True, copy=False)
    result_ids = fields.One2many("cmc.transfer.charge.result", "charge_id")
    posted_line_ids = fields.One2many("account.analytic.line", "cmc_transfer_charge_id", string="Posted Lines")

    _cmc_input_fields = (
        "company_id", "date_from", "date_to", "provider_id", "driver_id", "criterion", "markup_percent", "market_price",
        "negotiated_price", "abc_activity_id", "element_id", "line_ids",
    )
    _cmc_result_fields = ("result_ids", "posted_line_ids")

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        if any(charge.date_to < charge.date_from for charge in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))

    def _cmc_reset_values(self):
        return {
            **super()._cmc_reset_values(),
            **dict.fromkeys(
                ("provider_cost", "provider_variable_cost", "service_units", "unit_cost", "unit_variable_cost", "transfer_price",
                 "internal_margin_total"),
                0.0,
            ),
        }

    def unlink(self):
        if self.filtered(lambda charge: charge.state == "done"):
            raise UserError(self.env._("A computed chargeback cannot be deleted: reset it to draft first."))
        return super().unlink()

    # ---- compute ------------------------------------------------------------

    def _cmc_price(self, unit_cost, unit_variable_cost):
        """Transfer price of the criterion (Fazzini 12.7)."""
        criterion = self.criterion
        if criterion == "marginal":
            return unit_variable_cost
        if criterion == "full":
            return unit_cost
        if criterion == "cost_plus":
            return unit_cost * (1 + self.markup_percent / 100)
        if criterion == "abc":
            rate = self.env["cmc.rate"]._cmc_lookup(self.abc_activity_id, self.driver_id, self.date_from, company=self.company_id)
            if not rate:
                raise UserError(self.env._("No ABC rate is published for the activity and the service unit."))
            self._cmc_record_usage(self.env[rate.origin_model].browse(rate.origin_id))
            return rate.value
        price = self.market_price if criterion == "market" else self.negotiated_price
        if price <= 0:
            raise UserError(self.env._("The %s price must be positive.", dict(CRITERIA)[criterion].lower()))
        return price

    def _cmc_primary_cost(self, variable_only=False):
        self.ensure_one()
        domain = Domain([
            ("cmc_excluded", "=", False), ("company_id", "in", [False, self.company_id.id]), ("date", ">=", self.date_from),
            ("date", "<=", self.date_to), ("cmc_transfer_charge_id", "=", False), (self.provider_id.plan_id._column_name(), "=", self.provider_id.id),
        ])
        if variable_only:
            domain &= Domain("cmc_behavior", "=", "variable")
        # why sudo: a certified figure must not depend on who presses Compute (same scope as _cmc_cost_groups)
        return sum(row[-1] for row in self.env["account.analytic.line"].sudo()._cmc_cost_read_group(domain))

    def _cmc_compute(self):
        self.ensure_one()
        lines = self.line_ids
        units = sum(lines.mapped("quantity"))
        if units <= 0:
            raise UserError(self.env._("The receivers consume no service unit: there is nothing to charge."))
        company, rounding = self.company_id, self.currency_id.rounding
        provider = self.provider_id
        # RN-002: primary cost only, an earlier chargeback of the same span must not relieve the provider twice
        total, variable = (float_round(self._cmc_primary_cost(variable_only), precision_rounding=rounding) for variable_only in (False, True))
        if total <= 0:
            raise UserError(self.env._("The provider has no cost in the period: there is nothing to charge."))
        unit_cost, unit_variable = total / units, variable / units
        price = self._cmc_price(unit_cost, unit_variable)
        weights = {line.id: line.quantity for line in lines}
        shares = {"fixed": spread(total - variable, weights, rounding), "variable": spread(variable, weights, rounding)}
        # AD-110: the chargeback moves the full cost, the margin of the price stays a report figure
        postings = [(provider, behavior, amount) for behavior, amount in (("fixed", total - variable), ("variable", variable))]
        postings += [(line.receiver_id, behavior, -shares[behavior][line.id]) for line in lines for behavior in shares]
        self.env["account.analytic.line"].create([
            {
                "name": self.name, "date": self.date_to, "company_id": company.id, "amount": amount,
                center.plan_id._column_name(): center.id, "cmc_cost_element_id": self.element_id.id, "cmc_behavior": behavior,
                "cmc_transfer_charge_id": self.id,
            }
            for center, behavior, amount in postings if amount
        ])
        results = []
        for line in lines:
            cost = -(shares["fixed"][line.id] + shares["variable"][line.id])
            charge = -float_round(line.quantity * price, precision_rounding=rounding)
            results.append({
                "charge_id": self.id, "receiver_id": line.receiver_id.id, "quantity": line.quantity, "cost_amount": cost,
                "charge_amount": charge, "internal_margin": float_round(cost - charge, precision_rounding=rounding),
            })
        # why sudo: results are read-only for users; rates of a run are engine rows
        self.env["cmc.transfer.charge.result"].sudo().create(results)
        month = month_start(self.date_from)
        self.env["cmc.rate"].sudo()._cmc_publish(self, [
            {"holder": provider, "driver": self.driver_id, "component": component, "date": month, "value": value}
            for component, value in (("total", unit_cost), ("variable", unit_variable))
        ])
        self.write({
            "provider_cost": -total, "provider_variable_cost": -variable, "service_units": units, "unit_cost": unit_cost,
            "unit_variable_cost": unit_variable, "transfer_price": price,
            "internal_margin_total": float_round(sum(r["internal_margin"] for r in results), precision_rounding=rounding),
        })


class CmcTransferChargeLine(models.Model):
    _name = "cmc.transfer.charge.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Chargeback Receiver"
    _order = "id"
    _check_company_auto = True
    _cmc_run_field = "charge_id"

    charge_id = fields.Many2one("cmc.transfer.charge", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="charge_id.company_id", store=True, index=True)
    receiver_id = fields.Many2one("account.analytic.account", string="Receiver Center", required=True, check_company=True)
    quantity = fields.Float(string="Service Units")

    @api.constrains("quantity", "receiver_id", "charge_id")
    def _check_line(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(self.env._("The service units of a receiver must be positive."))
            if line.receiver_id == line.charge_id.provider_id:
                raise ValidationError(self.env._("A center cannot charge itself."))


class CmcTransferChargeResult(models.Model):
    _name = "cmc.transfer.charge.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Chargeback Result"
    _order = "id"

    charge_id = fields.Many2one("cmc.transfer.charge", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="charge_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="charge_id.currency_id")
    receiver_id = fields.Many2one("account.analytic.account", string="Receiver Center")
    quantity = fields.Float(string="Service Units")
    cost_amount = fields.Monetary()
    charge_amount = fields.Monetary()
    internal_margin = fields.Monetary()
